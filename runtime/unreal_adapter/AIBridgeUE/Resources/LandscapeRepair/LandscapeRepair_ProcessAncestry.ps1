# Snapshot ancestry without WMI or opening protected process modules.
if(-not ('AIBridgeLandscapeProcessTreeV1' -as [type])){
Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public static class AIBridgeLandscapeProcessTreeV1 {
 [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
 public struct Entry { public uint size, usage, pid; public UIntPtr heap; public uint module, threads, parent; public int priority; public uint flags; [MarshalAs(UnmanagedType.ByValTStr, SizeConst=260)] public string name; }
 [DllImport("kernel32.dll", SetLastError=true)] static extern IntPtr CreateToolhelp32Snapshot(uint flags,uint pid);
 [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern bool Process32FirstW(IntPtr h,ref Entry e);
 [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern bool Process32NextW(IntPtr h,ref Entry e);
 [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);
 public static Dictionary<uint,uint> Parents(){
  IntPtr h=CreateToolhelp32Snapshot(2,0); if(h==new IntPtr(-1)) throw new System.ComponentModel.Win32Exception();
  try{var result=new Dictionary<uint,uint>(); Entry e=new Entry(); e.size=(uint)Marshal.SizeOf(typeof(Entry));
   if(!Process32FirstW(h,ref e)) throw new System.ComponentModel.Win32Exception();
   do{result[e.pid]=e.parent;}while(Process32NextW(h,ref e)); return result;
  }finally{CloseHandle(h);}
 }
}
'@
}
function Get-AIBridgeProcessAncestry([uint32]$ProcessId){
 $parents=[AIBridgeLandscapeProcessTreeV1]::Parents();$cursor=$ProcessId;$seen=@{};$chain=@()
 for($i=0;$i -lt 64 -and $cursor -gt 4;$i++){if($seen.ContainsKey($cursor)){throw 'PROCESS_ANCESTRY_CYCLE'};$seen[$cursor]=$true;$chain+=([int64]$cursor);if(!$parents.ContainsKey($cursor)){break};$cursor=$parents[$cursor]}
 return $chain
}
