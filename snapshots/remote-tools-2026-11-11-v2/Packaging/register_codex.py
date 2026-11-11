"""Register only this portable Bridge MCP; preserve the rest of Codex config."""
import argparse
import json
import re
import shutil
import sys
import time
import tomllib
from pathlib import Path


def registration(package_root, config_path, *, replace_existing=False, dry_run=False):
    root=Path(package_root).resolve()
    config=Path(config_path).expanduser().resolve()
    python=root/'_System/.venv/Scripts/python.exe'
    helper=root/'Integrations/CodexBridge/bridge_mcp.py'
    if not python.is_file() or not helper.is_file():
        raise RuntimeError('Run _System/bootstrap.bat first; packaged Python/helper not found')
    original=config.read_text(encoding='utf-8-sig') if config.exists() else ''
    parsed=tomllib.loads(original)
    current=parsed.get('mcp_servers',{}).get('ta_bridge')
    command=str(python).replace('\\','/')
    args=[str(helper).replace('\\','/')]
    if current and current.get('command')==command and current.get('args')==args and current.get('enabled',True):
        return {'changed':False,'config':str(config),'server':'ta_bridge'}
    if current and not replace_existing:
        raise RuntimeError('ta_bridge already targets another installation. Inspect it first; --replace-existing makes a backup and replaces only this server.')
    block='\n[mcp_servers.ta_bridge]\ncommand = '+json.dumps(command)+'\nargs = '+json.dumps(args)+'\nstartup_timeout_sec = 15\ntool_timeout_sec = 30\n'
    if current:
        # Refuse unusual TOML spellings instead of guessing or dropping neighboring settings.
        pattern=r'(?ms)^\[mcp_servers\.ta_bridge\][ \t]*\r?\n.*?(?=^\[|\Z)'
        if not re.search(pattern,original): raise RuntimeError('Nonstandard ta_bridge table; merge manually')
        updated=re.sub(pattern,lambda _:block.lstrip('\n'),original,count=1)
    else: updated=original.rstrip()+'\n'+block
    result=tomllib.loads(updated)
    assert result['mcp_servers']['ta_bridge']['command']==command
    if dry_run: return {'changed':True,'dry_run':True,'config':str(config),'server':result['mcp_servers']['ta_bridge']}
    config.parent.mkdir(parents=True,exist_ok=True)
    backup=None
    if config.exists():
        backup=config.with_name(config.name+'.before_bridge_'+str(time.time_ns()))
        shutil.copy2(config,backup)
    temporary=config.with_name(config.name+'.bridge_tmp_'+str(time.time_ns()))
    temporary.write_text(updated,encoding='utf-8')
    temporary.replace(config)
    return {'changed':True,'config':str(config),'backup':str(backup) if backup else None,'server':'ta_bridge'}


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',type=Path,default=Path.home()/'.codex/config.toml')
    parser.add_argument('--replace-existing',action='store_true')
    parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--if-codex-installed',action='store_true')
    options=parser.parse_args()
    if options.if_codex_installed and not options.config.parent.exists():
        print('No Codex profile detected. Bridge is ready; MCP registration is optional.')
        sys.exit(0)
    print(json.dumps(registration(Path(__file__).resolve().parents[1],options.config,
        replace_existing=options.replace_existing,dry_run=options.dry_run),ensure_ascii=False,indent=2))
