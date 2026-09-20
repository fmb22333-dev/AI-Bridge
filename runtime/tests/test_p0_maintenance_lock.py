from ai_bridge.adapters.bridge_admin import BridgeAdminExecutor


def test_maintenance_lock_rejects_foreign_transaction(tmp_path):
    obj=object.__new__(BridgeAdminExecutor)
    obj.maintenance_lock_file=tmp_path/'maintenance.json'
    obj._maintenance_acquire('tx-a')
    state=obj._maintenance_read()
    assert state['transaction_id']=='tx-a'
    obj._maintenance_require('tx-a')
    try:
        obj._maintenance_require('tx-b')
    except Exception as exc:
        assert 'maintenance' in str(exc).lower() or 'transaction' in str(exc).lower()
    else:
        raise AssertionError('foreign transaction was accepted')
    obj._maintenance_release('tx-a')
    assert obj._maintenance_read() is None
