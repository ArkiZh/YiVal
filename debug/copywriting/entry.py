import secret


if __name__ == '__main__':
    import sys
    from yival.__main__ import main
    sys.argv.extend("run copywriting.yml".split())
    main()
    print("Done")
