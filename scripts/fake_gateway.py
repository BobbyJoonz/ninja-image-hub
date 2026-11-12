"""Shared fake gateway used by tests and the browser-e2e seed.

It answers only the endpoints the panel probes, captures every generation request to
``requests.jsonl`` and can return a scripted sequence of HTTP responses. It never talks to
ChatGPT and never consumes provider quota.
"""

import base64
import io
import json
from pathlib import Path

from PIL import Image

DEFAULT_RESPONSE = {
    "status": 200,
    "applied": {
        "thinking_mode": "extended",
        "thinking_verified": True,
        "requested_verified": True,
        "thinking_fallback": None,
        "thinking_mode_mixed": False,
    },
}


def png_bytes(size=(9, 7), color="green"):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def write_fake_gateway(
    base_dir,
    *,
    color="green",
    fail=False,
    responses=None,
    name="fake-gateway",
    image_bytes=None,
):
    """Create a gateway workspace and return ``(vendor_dir, capture_path)``."""
    vendor = Path(base_dir) / name
    (vendor / "src").mkdir(parents=True, exist_ok=True)
    (vendor / "node_modules").mkdir(exist_ok=True)
    (vendor / "public").mkdir(exist_ok=True)
    encoded = base64.b64encode(image_bytes or png_bytes(color=color)).decode()
    if responses is None:
        responses = [dict(DEFAULT_RESPONSE)]
    if fail:
        responses = [{"status": 500, "body": {"error": {"message": "boom"}}}]
    capture = vendor / "requests.jsonl"
    node_js = r"""const http=require('http'), fs=require('fs');
const PORT=process.env.PORT;
const CAPTURE=__CAPTURE__;
const RESPONSES=__RESPONSES__;
let call=0;
const app=http.createServer((req,res)=>{
  if(req.url.startsWith('/health')) { res.setHeader('Content-Type','application/json');res.end('{"status":"ok"}');return; }
  if(req.url.startsWith('/metrics')) { res.setHeader('Content-Type','text/plain');res.end('gpt_web_gateway_session_valid 1\n');return; }
  if(req.url.startsWith('/login/screenshot')) {res.setHeader('Content-Type','image/jpeg');res.end(Buffer.from('ffd8ffd9','hex'));return;}
  if(req.url==='/login' && req.method==='GET') {res.setHeader('Content-Type','text/html');res.end('<html>fake login</html>');return;}
  if(req.url.startsWith('/login/')) {res.setHeader('Content-Type','application/json');res.end('{"ok":true}');return;}
  if(req.url.startsWith('/v1/images/generations') || req.url.startsWith('/v1/chat/completions')) {
    if(req.headers.authorization !== 'Bearer '+process.env.API_KEY){res.writeHead(401);res.end();return;}
    let chunks=[];
    req.on('data',(c)=>chunks.push(c));
    req.on('end',()=>{
      const raw=Buffer.concat(chunks);
      const contentType=req.headers['content-type']||'';
      let parsed=null;
      if(!contentType.toLowerCase().includes('multipart')) {
        try { parsed=JSON.parse(raw.toString('utf8')||'{}'); } catch(e) { parsed=null; }
      }
      fs.appendFileSync(CAPTURE, JSON.stringify({url:req.url, content_type:contentType, body_b64:raw.toString('base64'), json:parsed})+'\n');
      const spec=RESPONSES[Math.min(call, RESPONSES.length-1)]; call += 1;
      if(spec.status && spec.status !== 200) {
        res.writeHead(spec.status, {'Content-Type':'application/json'});
        res.end(JSON.stringify(spec.body || {error:{message:'gateway failed'}}));
        return;
      }
      if(req.url.startsWith('/v1/chat/completions')) {
        res.setHeader('Content-Type','application/json');
        res.end(JSON.stringify({choices:[{message:{content:String(spec.text || '{"score": 9, "issues": [], "fixed_prompt": ""}')}}]}));
        return;
      }
      const payload={data:[{b64_json:__ENCODED__}]};
      if(spec.applied) payload.applied=spec.applied;
      if(spec.requested !== false) payload.requested={thinking_mode:'extended'};
      res.setHeader('Content-Type','application/json');
      res.end(JSON.stringify(payload));
    });
    return;
  }
  res.writeHead(404);res.end('not found');
});
app.listen(PORT, (err) => { if(err) throw err; });
""".replace("__ENCODED__", json.dumps(encoded)).replace(
        "__CAPTURE__", json.dumps(str(capture))
    ).replace("__RESPONSES__", json.dumps(responses))
    (vendor / "src" / "server.js").write_text(node_js, encoding="utf-8")
    return vendor, capture
