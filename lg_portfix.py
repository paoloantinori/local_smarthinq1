"""Fix upstream port for LG ThinQ servers — intercept at connection level"""
from mitmproxy import ctx


class LGPortFix:
    def server_connect(self, data) -> None:
        """Change upstream port from 443 to 46030 BEFORE connection is established"""
        server = data.server
        addr = server.address
        if addr and isinstance(addr, tuple) and len(addr) == 2:
            host, port = addr
            host_str = str(host)
            if "lgthinq" in host_str or "lgcloud" in host_str:
                if port == 443:
                    server.address = (host, 46030)
                    ctx.log.info(f"🔧 UPSTREAM PORT FIX: {host}:{port} → {host}:46030")


addons = [LGPortFix()]

