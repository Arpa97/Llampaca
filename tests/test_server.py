def test_port_scan_finds_available_port():
    # Bind a socket to consume a port
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    used_port = s.getsockname()[1]

    from llampaca.engine.server import is_port_in_use
    assert is_port_in_use(used_port)
    s.close()
