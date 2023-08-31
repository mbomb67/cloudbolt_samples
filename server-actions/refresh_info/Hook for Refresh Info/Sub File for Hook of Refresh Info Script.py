def run(*args, **kwargs):
    servers = kwargs.get("servers", [])
    for server in servers:
        server.refresh_info()
