import struct, select, signal, socket
from pysigset_exterminator import suspended_signals

class SshSocket(object):
    def __init__(self, channel):
        self._channel = channel

    def send_bytes(self, msg):
        self._channel.send(msg)

    def recv_bytes(self, size):
        msg = ''
        while size > 0:
            try:
                part = self._channel.recv(size)
            except socket.timeout:
                continue
            if len(part) == 0:
                raise EOFError
            size -= len(part)
            msg += part
        return msg

    def fileno(self):
        return self._channel.fileno()

    def poll(self):
        return self._channel.recv_ready()

class StdioSocket(object):
    def __init__(self, infile, outfile):
        self._infile = infile
        self._outfile = outfile
        self._eof = False

    def send_bytes(self, msg):
        self._outfile.write(msg)
        self._outfile.flush()

    def recv_bytes(self, size):
        if self._eof:
            raise EOFError
        msg = self._infile.read(size)
        if len(msg) < size:
            self._eof = True
        if len(msg) == 0:
            raise EOFError
        return msg

    def fileno(self):
        return self._infile.fileno()

    def poll(self):
        return True

class ProtocolSocket(object):
    def __init__(self, sock):
        self._sock = sock

    def send_packet(self, msg):
        with suspended_signals(signal.SIGINT):
            self._sock.send_bytes(struct.pack('I', len(msg)))
            self._sock.send_bytes(msg+'\n')

    def recv_packet(self):
        with suspended_signals(signal.SIGINT):
            size = int(struct.unpack('I', self._sock.recv_bytes(4))[0])
            msg = self._sock.recv_bytes(size+1)
            assert msg[-1] == '\n'
            return msg[:-1]

    def fileno(self):
        return self._sock.fileno()

    def poll(self, timeout=0):
        ready = select.select([self._sock], [], [], timeout)[0]
        return len(ready) > 0

    def close(self):
        self._sock.close()

    @staticmethod
    def select(socks, timeout=None):
        ready = select.select(socks, [], [], timeout)[0]
        return filter(lambda sock: sock.poll(), ready)

