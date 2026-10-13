"""PyInstaller entry point: double-click opens the native desktop window."""
if __name__ == "__main__":
    # PyInstaller's override routes worker/resource-tracker invocations away
    # from desktop startup. Dependencies can spawn these helpers on macOS too.
    from multiprocessing import freeze_support

    freeze_support()

    import sys

    if sys.argv[1:] == ["--sync-recovery-verify"]:
        from camoufox_pm.sync.recovery import recovery_worker_main

        raise SystemExit(recovery_worker_main())

    from camoufox_pm.desktop import finish_packaged_desktop, main

    main()
    finish_packaged_desktop()
