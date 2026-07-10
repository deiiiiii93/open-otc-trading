from app.services.deep_agent import channel_registry as cr


def test_commit_registry_swaps_under_lock():
    reg = cr.load_from_path(cr._yaml_path())
    cr.configure_registry(None)  # reset cache
    cr.commit_registry(reg)
    assert cr.get_registry() is reg
    cr.configure_registry(None)


def test_reload_reads_file_under_lock():
    # reload holds _LOCK across the file read: a thread that grabs _LOCK first
    # blocks reload from returning a stale snapshot.
    import threading
    started = threading.Event()
    release = threading.Event()

    def hold_lock():
        with cr._LOCK:
            started.set()
            release.wait(timeout=5)

    t = threading.Thread(target=hold_lock)
    t.start()
    started.wait(timeout=5)
    done = threading.Event()

    def do_reload():
        cr.reload()
        done.set()

    r = threading.Thread(target=do_reload)
    r.start()
    # reload must NOT complete while the lock is held elsewhere
    assert not done.wait(timeout=0.5)
    release.set()
    t.join()
    r.join()
    assert done.is_set()
    cr.configure_registry(None)
