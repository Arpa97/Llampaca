def test_port_scan_finds_available_port():
    # Bind a socket to consume a port
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    # is_port_in_use() probes with connect_ex, so binding alone is not enough:
    # a bound-but-not-listening socket refuses the connection and the check
    # correctly reports the port as free. Listen, as a real server would.
    s.listen(1)
    used_port = s.getsockname()[1]

    from llampaca.engine.server import is_port_in_use
    assert is_port_in_use(used_port)
    s.close()
