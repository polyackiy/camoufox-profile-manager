"""PyInstaller entry point: double-click opens the native desktop window."""
if __name__ == "__main__":
    # PyInstaller's override routes worker/resource-tracker invocations away
    # from desktop startup. Dependencies can spawn these helpers on macOS too.
    from multiprocessing import freeze_support

    freeze_support()

    from camoufox_pm.desktop import main

    main()
