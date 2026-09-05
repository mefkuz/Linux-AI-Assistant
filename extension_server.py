import json
import threading
import queue
from http.server import HTTPServer, BaseHTTPRequestHandler
from PyQt6.QtCore import QObject, pyqtSignal

class ExtensionSignals(QObject):
    data_received = pyqtSignal(dict)

signals = ExtensionSignals()

# Global queues for SSE clients
sse_clients = []

def send_browser_command(command_dict):
    msg = f"data: {json.dumps(command_dict)}\n\n"
    for q in sse_clients:
        try:
            q.put(msg)
        except:
            pass

class ExtensionRequestHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        if self.path == '/events':
            self.send_response(200)
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Content-type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Connection', 'keep-alive')
            self.end_headers()
            
            client_queue = queue.Queue()
            sse_clients.append(client_queue)
            try:
                # Send initial ping to confirm connection
                self.wfile.write(b"data: {\"action\": \"ping\"}\n\n")
                self.wfile.flush()
                while True:
                    msg = client_queue.get()
                    self.wfile.write(msg.encode('utf-8'))
                    self.wfile.flush()
            except Exception as e:
                pass
            finally:
                if client_queue in sse_clients:
                    sse_clients.remove(client_queue)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        
        try:
            data = json.loads(post_data.decode('utf-8'))
            signals.data_received.emit(data)
            
            self.send_response(200)
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"status": "success"}).encode('utf-8'))
        except Exception as e:
            self.send_response(400)
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode('utf-8'))

    def log_message(self, format, *args):
        pass

from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

def start_server():
    server = ThreadingHTTPServer(('127.0.0.1', 8765), ExtensionRequestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    
    # Return the module namespace so we can call send_browser_command from gui_main
    import sys
    return sys.modules[__name__]
