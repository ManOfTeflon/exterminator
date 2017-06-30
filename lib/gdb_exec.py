import os, sys
from vim_exterminator import RemoteGdb

if __name__ == '__main__':
    port = int(sys.argv[1])
    cmd = sys.argv[2]

    print("'%s'" % cmd)
    gdb = RemoteGdb(None, '127.0.0.1', int(port), name="cmd" + str(os.getpid()))
    gdb.send_exec(cmd)

