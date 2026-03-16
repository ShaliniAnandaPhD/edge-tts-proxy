#!/usr/bin/env python3
"""
🎙️ Edge TTS HTTP Proxy Server

Lightweight HTTP server wrapping the Python `edge_tts` library.
Same library cricket uses for debate audio — proven, reliable, free.

Runs locally or on any server. The frontend calls this instead of
the broken Supabase → Bing WebSocket path.

Usage:
    python3 scripts/edge_tts_server.py              # Start on port 5050
    python3 scripts/edge_tts_server.py --port 8080   # Custom port

Test:
    curl -X POST http://localhost:5050/synthesize \
         -H "Content-Type: application/json" \
         -d '{"text":"Welcome to the NBA Huddle","voice":"en-US-GuyNeural"}'
"""

import asyncio
import base64
import io
import json
import os
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

# ─── CONFIG ────────────────────────────────────────

PORT = int(os.environ.get("PORT", 0)) or (int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 5050)

# NBA agent → Edge TTS voice mapping (same voices as frontend)
VOICE_MAP = {
    "The PickMaster": {"voice": "en-US-GuyNeural",         "rate": "+5%",  "pitch": "+0Hz"},
    "StatLine":       {"voice": "en-US-ChristopherNeural", "rate": "+0%",  "pitch": "-2Hz"},
    "HoopsTake":      {"voice": "en-US-SteffanNeural",     "rate": "+12%", "pitch": "+3Hz"},
    "CourtVision":    {"voice": "en-US-JennyNeural",       "rate": "+0%",  "pitch": "+0Hz"},
    "ScriptMaster":   {"voice": "en-US-AndrewNeural",      "rate": "+8%",  "pitch": "-1Hz"},
    "Global Game":    {"voice": "en-US-BrianNeural",       "rate": "-3%",  "pitch": "+0Hz"},
}

# Hindi/Spanish voice overrides
LOCALIZED_VOICES = {
    # Hindi
    "hi-IN-MadhurNeural", "hi-IN-SwaraNeural", "hi-IN-HemantNeural",
    "hi-IN-AaravNeural", "hi-IN-AnanyaNeural", "hi-IN-KavyaNeural",
    "hi-IN-KunalNeural", "hi-IN-RehaanNeural", "hi-IN-ArjunNeural",
    # Spanish
    "es-ES-AlvaroNeural", "es-MX-DaliaNeural", "es-ES-ElviraNeural",
    "es-MX-JorgeNeural", "es-MX-CeciliaNeural", "es-US-AlonsoNeural",
    "es-US-PalomaNeural", "es-CO-SalomeNeural", "es-CO-GonzaloNeural",
    # Arabic
    "ar-SA-HamedNeural", "ar-SA-ZariyahNeural", "ar-AE-FatimaNeural",
    "ar-AE-HamdanNeural", "ar-EG-ShakirNeural", "ar-EG-SalmaNeural",
    "ar-QA-MoazNeural", "ar-JO-SanaNeural", "ar-KW-FahedNeural",
    # Tagalog
    "fil-PH-AngeloNeural", "fil-PH-BlessicaNeural"
}


# ─── TTS ENGINE ────────────────────────────────────

async def synthesize(text: str, voice: str = "en-US-GuyNeural",
                     rate: str = "+0%", pitch: str = "+0Hz") -> bytes:
    """Generate MP3 audio using edge_tts."""
    import edge_tts

    communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate, pitch=pitch)
    audio_buffer = io.BytesIO()

    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_buffer.write(chunk["data"])

    audio_buffer.seek(0)
    return audio_buffer.read()


# ─── HTTP HANDLER ──────────────────────────────────

class TTSHandler(BaseHTTPRequestHandler):
    """Handle TTS requests via REST API."""

    def do_OPTIONS(self):
        """CORS preflight."""
        self.send_response(200)
        self._cors_headers()
        self.end_headers()

    def do_GET(self):
        """Health check."""
        path = urlparse(self.path).path
        if path in ("/", "/health"):
            self.send_response(200)
            self._cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "status": "ok",
                "service": "edge-tts-proxy",
                "voices": list(VOICE_MAP.keys()),
            }).encode())
        elif path == "/voices":
            self.send_response(200)
            self._cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(VOICE_MAP).encode())
        else:
            self.send_error(404)

    def do_HEAD(self):
        """Handle HEAD requests (useful for Render health checks)."""
        path = urlparse(self.path).path
        if path in ("/", "/health", "/voices"):
            self.send_response(200)
            self._cors_headers()
            self.end_headers()
        else:
            self.send_error(404)

    def do_POST(self):
        """Synthesize speech."""
        path = urlparse(self.path).path
        if path != "/synthesize":
            self.send_error(404)
            return

        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_length))
        except (json.JSONDecodeError, ValueError):
            self._error(400, "Invalid JSON body")
            return

        text = body.get("text", "").strip()
        if not text:
            self._error(400, "Missing 'text' field")
            return
        if len(text) > 5000:
            self._error(400, "Text too long (max 5000 chars)")
            return

        # Resolve voice: prefer explicit localized voice from frontend,
        # then fall back to agent name lookup, then default
        agent_name = body.get("agentName", "")
        voice = body.get("voice", "en-US-GuyNeural")
        rate = body.get("rate", "+0%")
        pitch = body.get("pitch", "+0Hz")

        # If the frontend sent a localized voice (Hindi/Spanish), use it directly
        if voice in LOCALIZED_VOICES:
            # Keep the localized voice, don't override with agent's English voice
            pass
        elif agent_name and agent_name in VOICE_MAP:
            # Use agent's default English voice
            cfg = VOICE_MAP[agent_name]
            voice = cfg["voice"]
            rate = cfg["rate"]
            pitch = cfg["pitch"]

        print(f"  🎤 {agent_name or voice} | {len(text)} chars | {voice} rate={rate} pitch={pitch}")

        try:
            loop = asyncio.new_event_loop()
            audio_bytes = loop.run_until_complete(synthesize(text, voice, rate, pitch))
            loop.close()
        except Exception as e:
            self._error(500, f"Synthesis failed: {e}")
            return

        if len(audio_bytes) == 0:
            self._error(500, "No audio generated")
            return

        audio_b64 = base64.b64encode(audio_bytes).decode("ascii")

        response = json.dumps({
            "audioContent": audio_b64,
            "format": "audio/mpeg",
            "provider": "edge-tts-python",
            "voice": voice,
            "size": len(audio_bytes),
        })

        self.send_response(200)
        self._cors_headers()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(response.encode())
        print(f"  ✅ {len(audio_bytes):,} bytes audio")

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def _error(self, code: int, msg: str):
        self.send_response(code)
        self._cors_headers()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"error": msg}).encode())
        print(f"  ❌ {code}: {msg}")

    def log_message(self, format, *args):
        """Suppress default access log."""
        pass


# ─── MAIN ──────────────────────────────────────────

def main():
    # Verify edge_tts is installed
    try:
        import edge_tts
        print(f"  ✅ edge_tts v{edge_tts.__version__ if hasattr(edge_tts, '__version__') else '?'}")
    except ImportError:
        print("❌ edge_tts not installed. Run: pip install edge-tts")
        sys.exit(1)

    server = HTTPServer(("0.0.0.0", PORT), TTSHandler)
    print(f"""
╔══════════════════════════════════════════════════════╗
║  🎙️  EDGE TTS PROXY SERVER                          ║
║  Port: {PORT:<47}║
║  Endpoint: http://localhost:{PORT}/synthesize{' ' * (14 - len(str(PORT)))}║
║  Voices: {len(VOICE_MAP)} NBA agents mapped{' ' * 27}║
╚══════════════════════════════════════════════════════╝
""")
    print("  Agents:")
    for name, cfg in VOICE_MAP.items():
        print(f"    {name:20s} → {cfg['voice']}")
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 Shutting down Edge TTS server")
        server.shutdown()


if __name__ == "__main__":
    main()
