from aldi.app import main

# BabelDOC (Mantener el diseño) starts processes, which import this module again on Windows and macOS.
if __name__ == "__main__":
    main()
