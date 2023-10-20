from yival.__main__ import main


if __name__ == '__main__':
    import sys
    sys.argv.extend("run simple.yml".split())
    main()
    print("Done")