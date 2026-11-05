"""由独立本地 HTML 生成页面预览和同源 Chromium 截图。"""

import base64
import hashlib
import json
from pathlib import Path


_SCRIPT = r"""
const data = JSON.parse(document.getElementById('page-data').textContent);
const stage = document.getElementById('stage');
const toolbar = document.getElementById('toolbar');
const host = document.getElementById('host');
const zoom = document.getElementById('zoom');
function setZoom(value) {
  stage.style.transform = `scale(${value})`;
  host.style.width = `${data.width * value}px`;
  host.style.height = `${data.height * value}px`;
}
zoom.addEventListener('change', () => setZoom(Number(zoom.value)));
window.smartuiReady = (async () => {
  stage.style.width = `${data.width}px`;
  stage.style.height = `${data.height}px`;
  stage.style.backgroundColor = data.background;
  setZoom(1);
  const fonts = [];
  for (const font of data.fonts) {
    const face = new FontFace(font.family, `url(${font.url})`);
    try { await face.load(); }
    catch (error) { throw new Error(`字体 ${font.sha256} 加载失败：${error.message}`); }
    if (face.status !== 'loaded') throw new Error(`字体 ${font.sha256} 未成功加载`);
    document.fonts.add(face);
    fonts.push({family: font.family, sha256: font.sha256, status: face.status});
  }
  const elements = [];
  for (const node of data.nodes) {
    const element = document.createElement('div');
    element.dataset.nodeId = node.id;
    element.className = 'node';
    element.style.left = `${node.x}px`;
    element.style.top = `${node.y}px`;
    element.style.width = `${node.width}px`;
    element.style.height = `${node.height}px`;
    if (node.kind === 'image') {
      const plan = node.patch_plan;
      const canvas = document.createElement('canvas');
      canvas.width = Math.ceil(plan.image_size[0]);
      canvas.height = Math.ceil(plan.image_size[1]);
      element.appendChild(canvas);
      element.style.width = `${plan.image_size[0]}px`;
      element.style.height = `${plan.image_size[1]}px`;
      element.style.left = `${node.x + plan.content_offset[0]}px`;
      element.style.top = `${node.y + plan.content_offset[1]}px`;
      const image = new Image();
      image.src = node.url;
      try { await image.decode(); }
      catch (error) { throw new Error(`图像节点 ${node.id} 解码失败：${error.message}`); }
      const context = canvas.getContext('2d');
      // 容器裁切不足一个像素的空白；不缩放整个 Canvas，保留固定角原始像素。
      for (const patch of plan.patches) {
        // 独立裁片限制插值采样范围，避免缩放时混入相邻固定边的颜色。
        const piece = document.createElement('canvas');
        piece.width = patch.source[2];
        piece.height = patch.source[3];
        piece.getContext('2d').drawImage(image, ...patch.source, 0, 0, piece.width, piece.height);
        context.drawImage(piece, ...patch.target);
      }
    } else {
      element.textContent = node.text;
      Object.assign(element.style, {
        // 描边沿字形边缘向两侧展开；内边距让左、右对齐也保留完整描边。
        padding: `${node.stroke_width / 2}px`,
        fontFamily: node.font_family, fontSize: `${node.font_size}px`,
        lineHeight: `${node.line_height}px`, textAlign: node.align,
        whiteSpace: node.wrap ? 'pre-wrap' : 'pre', overflowWrap: node.wrap ? 'anywhere' : 'normal',
        color: node.color, webkitTextStrokeWidth: `${node.stroke_width}px`,
        webkitTextStrokeColor: node.stroke_color, paintOrder: 'stroke fill'
      });
    }
    stage.appendChild(element);
    elements.push([node, element]);
  }
  await document.fonts.ready;
  await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  const origin = stage.getBoundingClientRect();
  const rect = bounds => [bounds.x - origin.x, bounds.y - origin.y, bounds.width, bounds.height];
  const measurements = [];
  for (const [node, element] of elements) {
    const box = element.getBoundingClientRect();
    let content = box;
    let overflow = false;
    if (node.kind === 'text') {
      const range = document.createRange();
      range.selectNodeContents(element);
      content = range.getBoundingClientRect();
      const stroke = node.stroke_width / 2;
      // scrollWidth/Height 包含末尾空行；Range 还能识别居中及右对齐的左侧溢出。
      overflow = element.scrollWidth > element.clientWidth || element.scrollHeight > element.clientHeight ||
        (node.text.length > 0 && (content.left - stroke < box.left - 0.02 ||
          content.right + stroke > box.right + 0.02 || content.top - stroke < box.top - 0.02 ||
          content.bottom + stroke > box.bottom + 0.02));
      if (overflow && node.overflow === 'error') {
        throw new Error(`文字节点 ${node.id} 超出文本框：框 ${JSON.stringify(rect(box))}，内容 ${JSON.stringify(rect(content))}`);
      }
    }
    measurements.push({id: node.id, box: rect(box), content_box: rect(content), overflow});
  }
  return {fonts, nodes: measurements};
})();
window.smartuiReady.catch(error => {
  document.getElementById('status').textContent = error.message;
  document.getElementById('status').style.color = '#ffabab';
});
window.smartuiForScreenshot = async () => {
  const evidence = await window.smartuiReady;
  toolbar.hidden = true;
  document.body.classList.add('screenshot');
  setZoom(1);
  await new Promise(resolve => requestAnimationFrame(resolve));
  return evidence;
};
"""


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _resource(path, expected, label, mime):
    path = Path(path)
    if not path.is_absolute() or not path.is_file():
        raise ValueError(f"{label} 文件不存在或不是绝对路径：{path}")
    content = path.read_bytes()
    digest = _sha(content)
    if digest != expected:
        raise ValueError(f"{label} 文件摘要与快照不一致：{path}")
    return f"data:{mime};base64,{base64.b64encode(content).decode('ascii')}"


def _html(page):
    fonts = {}
    nodes = []
    for node in page["nodes"]:
        item = dict(node)
        if node["kind"] == "image":
            item["url"] = _resource(node["source"], node["sha256"], f"图像节点 {node['id']}", "image/png")
        elif node["kind"] == "text":
            digest = node["font_sha256"]
            url = _resource(node["font_path"], digest, f"文字节点 {node['id']} 的字体", "font/otf")
            if digest not in fonts:
                fonts[digest] = {"family": f"SmartUIFont_{len(fonts)}", "sha256": digest, "url": url}
            item["font_family"] = fonts[digest]["family"]
        else:
            raise ValueError(f"浏览器预览不支持节点类型：{node['kind']}")
        # 独立 HTML 只携带实际内容，不保留本机资源路径。
        item.pop("source", None)
        item.pop("font_path", None)
        nodes.append(item)
    data = {"width": page["width"], "height": page["height"], "background": page["background"],
            "nodes": nodes, "fonts": list(fonts.values())}
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    for raw, escaped in (("&", "\\u0026"), ("<", "\\u003c"), (">", "\\u003e"),
                         ("\u2028", "\\u2028"), ("\u2029", "\\u2029")):
        payload = payload.replace(raw, escaped)
    script_hash = base64.b64encode(hashlib.sha256(_SCRIPT.encode()).digest()).decode("ascii")
    return """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; font-src data:; style-src 'unsafe-inline'; script-src 'sha256-""" + script_hash + """'; base-uri 'none'; form-action 'none'">
<title>SmartUI 页面预览</title><style>
* { box-sizing: border-box; }
body { margin: 0; background: #282828; }
#toolbar { height: 48px; display: flex; align-items: center; gap: 16px; padding: 8px 16px; color: white; font: 14px sans-serif; }
#toolbar[hidden] { display: none; }
#host { position: relative; margin: 16px; }
#stage { position: relative; overflow: hidden; transform-origin: 0 0; }
.node { position: absolute; margin: 0; padding: 0; border: 0; overflow: hidden; font-synthesis: none; }
.node > canvas { display: block; }
body.screenshot { background: transparent; }
body.screenshot #host { margin: 0; }
</style></head><body>
<div id="toolbar"><span>SmartUI 页面预览</span><label>缩放 <select id="zoom"><option value="0.25">25%</option><option value="0.5">50%</option><option value="1" selected>100%</option><option value="1.5">150%</option><option value="2">200%</option></select></label><span id="status"></span></div>
<div id="host"><div id="stage"></div></div>
<script type="application/json" id="page-data">""" + payload + "</script>\n<script>" + _SCRIPT + "</script></body></html>\n"


def _capture(html_path, png_path, width, height):
    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError as error:
        raise RuntimeError("浏览器预览需要 Playwright；请运行 python -m pip install playwright，然后运行 python -m playwright install chromium") from error
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except Error as error:
            raise RuntimeError("Chromium 启动失败；请运行 python -m playwright install chromium，并检查浏览器运行依赖。原始错误：" + str(error)) from error
        try:
            context = browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=1,
                                          color_scheme="light", reduced_motion="reduce", locale="zh-CN")
            context.set_offline(True)
            tab = context.new_page()
            tab.goto(html_path.as_uri(), wait_until="load")
            evidence = tab.evaluate("() => window.smartuiForScreenshot()")
            environment = tab.evaluate("() => ({device_scale_factor: devicePixelRatio, user_agent: navigator.userAgent})")
            tab.screenshot(path=str(png_path), type="png", omit_background=True, animations="disabled")
            return {"engine": "chromium", "version": browser.version, "viewport": {"width": width, "height": height},
                    **environment, **evidence}
        except Error as error:
            raise ValueError(f"浏览器页面渲染失败：{error}") from error
        finally:
            browser.close()


def render_page(directory: Path, page: dict) -> dict:
    """保存可离线打开的 HTML，以同一页面输出参考尺寸的 PNG 和渲染证据。"""
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    html_path = directory / "preview.html"
    png_path = directory / "overview.png"
    html_path.write_text(_html(page), encoding="utf-8", newline="\n")
    renderer = _capture(html_path, png_path, page["width"], page["height"])
    return {"html": {"path": str(html_path), "sha256": _sha(html_path.read_bytes())},
            "overview": {"path": str(png_path), "sha256": _sha(png_path.read_bytes()),
                         "size": [page["width"], page["height"]]}, "renderer": renderer}
