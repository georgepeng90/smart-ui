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
const overlay = document.getElementById('overlay');
function setZoom(value) {
  stage.style.transform = `scale(${value})`;
  host.style.width = `${data.width * value}px`;
  host.style.height = `${data.height * value}px`;
}
zoom.addEventListener('change', () => setZoom(Number(zoom.value)));
function effectColor({color, opacity}) {
  const rgb = Number.parseInt(color.slice(1), 16);
  return `rgb(${rgb >> 16} ${(rgb >> 8) & 255} ${rgb & 255} / ${opacity})`;
}
function effectInsets(stroke, shadow) {
  const halfStroke = stroke ? stroke.width / 2 : 0;
  if (!shadow) return {left: halfStroke, top: halfStroke, right: halfStroke, bottom: halfStroke};
  // CSS 投影沿字形轮廓生成；按设计像素为模糊与栅格化预留空间。
  const spread = shadow.blur > 0 ? Math.ceil(2 * shadow.blur) + 1 : 0;
  return {
    left: Math.max(halfStroke, spread - shadow.offset_x),
    top: Math.max(halfStroke, spread - shadow.offset_y),
    right: Math.max(halfStroke, spread + shadow.offset_x),
    bottom: Math.max(halfStroke, spread + shadow.offset_y)
  };
}
function startOfLine(range, text, top, from) {
  let low = from + 1, high = text.length;
  while (low < high) {
    const mid = Math.floor((low + high) / 2);
    range.setStart(text, mid - 1);
    range.setEnd(text, mid);
    if (range.getBoundingClientRect().top >= top) high = mid;
    else low = mid + 1;
  }
  return low - 1;
}
function lineTopsOf(range) {
  return [...new Set(Array.from(range.getClientRects(), rect => rect.top))];
}
function* measuredLines(node, text, range, lineTops, context, fontSize) {
  context.font = `${fontSize}px ${node.font_family}`;
  context.letterSpacing = `${node.letter_spacing}px`;
  let start = 0;
  for (let index = 0; index < lineTops.length; index++) {
    // 由 DOM 确定实际断行位置，Canvas 只补充行框未反映的字形外伸。
    const end = index + 1 < lineTops.length ? startOfLine(range, text, lineTops[index + 1], start) : text.length;
    range.setStart(text, start);
    range.setEnd(text, end);
    const line = range.getBoundingClientRect();
    const metrics = context.measureText(text.data.slice(start, end).replace(/\n$/, ''));
    yield {line, metrics};
    start = end;
  }
}
function textEffectBase(node, layout, range, content, context, lineTops, fontSize, lineHeight) {
  const top = layout.getBoundingClientRect().top;
  let left = content.left, right = content.right;
  let inkTop = top, inkBottom = top + (lineTops.length ? lineTops.at(-1) - lineTops[0] + lineHeight : 0);
  for (const {line, metrics} of measuredLines(node, layout.firstChild, range, lineTops, context, fontSize)) {
    const baseline = line.top + metrics.fontBoundingBoxAscent;
    left = Math.min(left, line.left - metrics.actualBoundingBoxLeft);
    right = Math.max(right, line.left + metrics.actualBoundingBoxRight);
    inkTop = Math.min(inkTop, baseline - metrics.actualBoundingBoxAscent);
    inkBottom = Math.max(inkBottom, baseline + metrics.actualBoundingBoxDescent);
  }
  return new DOMRect(left, inkTop, right - left, inkBottom - inkTop);
}
function visibleTextBox({node, layout, insets}, measurement, origin, context) {
  if (node.fill.opacity === 0 && !(node.stroke?.opacity > 0 && node.stroke.width > 0) &&
      !(node.shadow?.opacity > 0)) return null;
  const text = layout.firstChild;
  const range = document.createRange();
  // 行数裁切把后续行放在隐藏 span，首个文本节点只含仍显示的文字。
  range.selectNodeContents(text);
  const lineTops = lineTopsOf(range);
  let left = Infinity, top = Infinity, right = -Infinity, bottom = -Infinity;
  for (const {line, metrics} of measuredLines(node, text, range, lineTops, context, measurement.font_size)) {
    const {actualBoundingBoxLeft, actualBoundingBoxRight, actualBoundingBoxAscent, actualBoundingBoxDescent} = metrics;
    if (actualBoundingBoxLeft + actualBoundingBoxRight <= 0 ||
        actualBoundingBoxAscent + actualBoundingBoxDescent <= 0) continue;
    const baseline = measurement.baseline + line.top - lineTops[0];
    left = Math.min(left, line.left - origin.x - actualBoundingBoxLeft - insets.left);
    top = Math.min(top, baseline - actualBoundingBoxAscent - insets.top);
    right = Math.max(right, line.left - origin.x + actualBoundingBoxRight + insets.right);
    bottom = Math.max(bottom, baseline + actualBoundingBoxDescent + insets.bottom);
  }
  // 用字形和效果预算估计边界，再限制到浏览器实际使用的裁切范围。
  const [x, y, width, height] = measurement.box;
  left = Math.max(0, x, left);
  top = Math.max(0, y, top);
  right = Math.min(data.width, x + width, right);
  bottom = Math.min(data.height, y + height, bottom);
  return right > left && bottom > top ? [left, top, right - left, bottom - top] : null;
}
function textBaseline(layout) {
  const marker = document.createElement('span');
  marker.className = 'baseline-marker';
  layout.prepend(marker);
  const baseline = marker.getBoundingClientRect().top;
  marker.remove();
  return baseline;
}
function measureTextNode({node, element, layout, insets}, origin, context, fontSize, lineHeight) {
  const box = element.getBoundingClientRect();
  const range = document.createRange();
  range.selectNodeContents(layout);
  const content = range.getBoundingClientRect();
  const lineTops = lineTopsOf(range);
  const effectBase = textEffectBase(node, layout, range, content, context, lineTops, fontSize, lineHeight);
  const effects = new DOMRect(
    effectBase.x - insets.left, effectBase.y - insets.top,
    effectBase.width + insets.left + insets.right, effectBase.height + insets.top + insets.bottom
  );
  const lineOverflow = node.max_lines !== undefined && lineTops.length > node.max_lines;
  // 水平滚动尺寸补充 Range 的检查，垂直方向使用实际行高与字形度量。
  const overflow = lineOverflow || layout.scrollWidth > layout.clientWidth ||
    effects.left < box.left - 0.02 || effects.right > box.right + 0.02 ||
    effects.top < box.top - 0.02 || effects.bottom > box.bottom + 0.02;
  return {
    id: node.id, kind: node.kind, box: relativeRect(box, origin),
    layout_box: [node.x, node.y, node.width, node.height],
    content_box: relativeRect(content, origin), effects_bounds: relativeRect(effects, origin),
    effect_insets: insets, overflow, line_overflow: lineOverflow, line_count: lineTops.length,
    font_size: fontSize, line_height: lineHeight
  };
}
function relativeRect(rect, origin) {
  return [rect.x - origin.x, rect.y - origin.y, rect.width, rect.height];
}
function clipAfterLines(layout, maxLines) {
  const text = layout.firstChild;
  const range = document.createRange();
  range.selectNodeContents(layout);
  const top = lineTopsOf(range)[maxLines];
  const start = startOfLine(range, text, top, 0);
  range.setStart(text, start);
  range.setEnd(text, text.length);
  const hidden = document.createElement('span');
  hidden.style.visibility = 'hidden';
  range.surroundContents(hidden);
}
function fitText(entry, origin, context) {
  const {node, layout} = entry;
  let fontSize = node.font_size;
  let lineHeight = node.line_height;
  let result = measureTextNode(entry, origin, context, fontSize, lineHeight);
  while (result.overflow && node.overflow === 'shrink' && fontSize > node.min_font_size) {
    fontSize -= 1;
    lineHeight = node.line_height * fontSize / node.font_size;
    layout.style.fontSize = `${fontSize}px`;
    layout.style.lineHeight = `${lineHeight}px`;
    result = measureTextNode(entry, origin, context, fontSize, lineHeight);
  }
  if (result.overflow && node.overflow !== 'clip') {
    const lines = result.line_overflow ? `，max_lines=${node.max_lines}，实际 ${result.line_count} 行` : '';
    const limit = node.overflow === 'shrink' ? `，已尝试最小字号 min_font_size=${node.min_font_size}` : '';
    throw new Error(`文字节点 ${node.id} 超出文本框${lines}${limit}：框 ${JSON.stringify(result.box)}，内容 ${JSON.stringify(result.content_box)}，含效果 ${JSON.stringify(result.effects_bounds)}`);
  }
  result.baseline = textBaseline(layout) - origin.y;
  if (result.line_overflow) clipAfterLines(layout, node.max_lines);
  return result;
}
function alignBaselines(groups, entries, measurements, origin, context) {
  for (const group of groups) {
    const row = group.node_ids.map(id => measurements.get(id));
    if (row.some(item => item?.kind !== 'text' || item.line_count !== 1)) {
      throw new Error(`基线分组 ${group.id} 只接受单行文字`);
    }
    const target = Math.max(...row.map(item => item.baseline));
    for (const item of row) {
      const entry = entries.get(item.id);
      const offset = target - item.baseline;
      if (offset) entry.element.style.top = `${entry.node.y + offset}px`;
      const updated = offset ? measureTextNode(entry, origin, context, item.font_size, item.line_height) : item;
      if (updated.box[1] < group.y - 0.02 ||
          updated.box[1] + updated.box[3] > group.y + group.height + 0.02) {
        throw new Error(`文字节点 ${item.id} 对齐后超出基线分组 ${group.id} 的内容区域`);
      }
      if (offset) updated.baseline = textBaseline(entry.layout) - origin.y;
      measurements.set(item.id, updated);
    }
  }
}
function canvasVisibleBox(canvas, box) {
  const {width, height} = canvas;
  const pixels = canvas.getContext('2d').getImageData(0, 0, width, height).data;
  let left = width, top = height, right = 0, bottom = 0;
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      if (pixels[(y * width + x) * 4 + 3] === 0) continue;
      left = Math.min(left, x);
      top = Math.min(top, y);
      right = Math.max(right, x + 1);
      bottom = Math.max(bottom, y + 1);
    }
  }
  if (right <= left || bottom <= top) return null;
  return [box[0] + left, box[1] + top, Math.min(right, box[2]) - left, Math.min(bottom, box[3]) - top];
}
function intersects(a, b) {
  return Math.min(a[0] + a[2], b[0] + b[2]) > Math.max(a[0], b[0]) &&
    Math.min(a[1] + a[3], b[1] + b[3]) > Math.max(a[1], b[1]);
}
function contains(outer, inner) {
  return inner[0] >= outer[0] - 0.02 && inner[1] >= outer[1] - 0.02 &&
    inner[0] + inner[2] <= outer[0] + outer[2] + 0.02 &&
    inner[1] + inner[3] <= outer[1] + outer[3] + 0.02;
}
function layoutWarnings(groups, measurements, usages, checks) {
  const warnings = [];
  const groupById = new Map(groups.map(group => [group.id, group]));
  const subjects = new Map([...measurements, ...groups.map(group => [group.id, {...group, kind: 'group'}])]);
  const describe = ({id, kind}) => ({id, kind});
  const pairKey = (a, b) => JSON.stringify(a < b ? [a, b] : [b, a]);
  const allowed = new Set((checks.allow_overlap || []).map(([a, b]) => pairKey(a, b)));
  function checkSiblings(groupId, children, allowOverlap) {
    for (const [index, first] of children.entries()) {
      for (const second of children.slice(index + 1)) {
        if (!intersects(first.content_box, second.content_box)) continue;
        if (allowOverlap && allowed.has(pairKey(first.id, second.id))) continue;
        warnings.push({type: 'content_overlap', group_id: groupId, nodes: [describe(first), describe(second)]});
      }
    }
  }
  function checkVisualAlignment(group, children) {
    const tolerance = checks.visual_alignment_tolerance;
    if (tolerance === undefined) return;
    const axes = [];
    if ((group.direction === 'column' && group.align === 'center') ||
        (group.direction === 'overlay' && group.justify === 'center')) axes.push(['x', 0]);
    if ((group.direction === 'row' || group.direction === 'overlay') && group.align === 'center') {
      axes.push(['y', 1]);
    }
    for (const child of children) {
      const bounds = child.kind === 'group' ? child.content_box : child.visible_box;
      if (!bounds) continue;
      for (const [axis, index] of axes) {
        const center = box => box[index] + box[index + 2] / 2;
        const offset = center(bounds) - center(group.content_box);
        if (Math.abs(offset) > tolerance) {
          warnings.push({type: 'visual_alignment_offset', group_id: group.id, node_id: child.id,
            node_kind: child.kind, axis, offset, tolerance});
        }
      }
    }
  }
  function* descendants(group) {
    for (const id of group.node_ids) {
      const nested = groupById.get(id);
      if (nested) yield* descendants(nested);
      else yield measurements.get(id);
    }
  }
  const childIds = new Set(groups.flatMap(group => group.node_ids));
  checkSiblings(null, [...subjects.values()].filter(node => !childIds.has(node.id)), true);
  for (const group of groups) {
    const children = group.node_ids.map(id => subjects.get(id));
    if (group.direction !== 'overlay') {
      checkSiblings(group.id, children, group.direction === 'absolute');
    }
    checkVisualAlignment(group, children);
    if (!Object.hasOwn(usages, group.id)) continue;
    const usage = usages[group.id];
    if (usage.states.missing.length) {
      warnings.push({type: 'missing_states', group_id: group.id, component_id: usage.component_id,
        missing: usage.states.missing});
    }
    if (usage.states.stale.length) {
      warnings.push({type: 'stale_states', group_id: group.id, component_id: usage.component_id,
        stale: usage.states.stale});
    }
    for (const node of descendants(group)) {
      if (node.kind === 'text' && !contains(usage.text_rect, node.effects_bounds)) {
        warnings.push({type: 'text_outside_component_rect', group_id: group.id,
          component_id: usage.component_id, nodes: [describe(node)]});
      }
    }
  }
  for (const node of measurements.values()) {
    if (node.kind === 'text' && node.font_size < checks.min_font_size) {
      warnings.push({type: 'font_size_below_minimum', nodes: [describe(node)],
        font_size: node.font_size, min_font_size: checks.min_font_size});
    }
  }
  return warnings;
}
function addOverlays(groups, measurements, usages) {
  const layers = new Map();
  for (const input of toolbar.querySelectorAll('[data-layer]')) {
    const layer = document.createElement('div');
    layer.className = `overlay-layer overlay-${input.dataset.layer}`;
    layer.hidden = !input.checked;
    input.addEventListener('change', () => { layer.hidden = !input.checked; });
    overlay.append(layer);
    layers.set(input.dataset.layer, layer);
  }
  function mark(layer, id, type, box, text = id) {
    if (!box) return;
    const [x, y, width, height] = box;
    const shape = document.createElement('div');
    shape.className = `overlay-mark overlay-${type}`;
    shape.dataset.id = id;
    Object.assign(shape.style, {
      left: `${x}px`, top: `${y}px`, width: `${width}px`, height: `${height}px`
    });
    if (layer === 'guides' && type === 'rect') {
      const label = document.createElement('span');
      label.className = 'overlay-label';
      label.textContent = text;
      shape.append(label);
    }
    layers.get(layer).append(shape);
  }
  mark('guides', 'canvas-center-x', 'vertical-line', [data.width / 2, 0, 0, data.height]);
  mark('guides', 'canvas-center-y', 'line', [0, data.height / 2, data.width, 0]);
  if (data.safe_area) {
    const {left, top, right, bottom} = data.safe_area;
    mark('guides', 'safe-area', 'rect', [left, top, data.width - left - right, data.height - top - bottom]);
  }
  for (const group of groups) {
    if (!Object.hasOwn(data.unity_adaptation || {}, group.id)) {
      mark('guides', group.id, 'rect', group.box);
    }
    mark('content', group.id, 'rect', group.content_box);
    if (!Object.hasOwn(usages, group.id)) continue;
    const {text_rect, hit_rect, pivot} = usages[group.id];
    mark('guides', group.id, 'text-rect', text_rect);
    mark('guides', group.id, 'hit-rect', hit_rect);
    mark('guides', group.id, 'pivot', [pivot.x, pivot.y, 0, 0]);
  }
  for (const node of measurements.values()) {
    mark('content', node.id, 'rect', node.content_box);
    mark('visible', node.id, 'rect', node.visible_box);
    if (node.kind === 'text') {
      mark('baselines', node.id, 'line', [node.box[0], node.baseline, node.box[2], 0]);
    }
  }
  for (const [id, settings] of Object.entries(data.unity_adaptation || {})) {
    const box = groups.find(group => group.id === id)?.box || measurements.get(id)?.layout_box;
    const details = [id];
    if (settings.anchor_min) details.push(`anchor [${settings.anchor_min}]–[${settings.anchor_max}]`);
    if (settings.pivot) details.push(`pivot [${settings.pivot}]`);
    for (const [key, name] of [['viewport_insets', 'viewport'], ['content_insets', 'content']]) {
      if (settings[key]) {
        const {top, right, bottom, left} = settings[key];
        details.push(`${name} [${top},${right},${bottom},${left}]`);
      }
    }
    mark('guides', id, 'rect', box, details.join(' · '));
  }
}
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
  const elements = new Map();
  for (const node of data.nodes) {
    const element = document.createElement('div');
    element.dataset.nodeId = node.id;
    element.className = 'node';
    element.style.left = `${node.x}px`;
    element.style.top = `${node.y}px`;
    element.style.width = `${node.width}px`;
    element.style.height = `${node.height}px`;
    let layout, insets;
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
      const stroke = node.stroke?.opacity > 0 && node.stroke.width > 0 ? node.stroke : null;
      const shadow = node.shadow?.opacity > 0 ? node.shadow : null;
      insets = effectInsets(stroke, shadow);
      const contentLeft = node.content_insets?.left ?? 0;
      const contentRight = node.content_insets?.right ?? 0;
      layout = document.createElement('div');
      layout.textContent = node.text;
      Object.assign(layout.style, {
        position: 'absolute', left: `${insets.left + contentLeft}px`, top: `${insets.top}px`,
        width: `${Math.max(0, node.width - insets.left - insets.right - contentLeft - contentRight)}px`,
        height: `${Math.max(0, node.height - insets.top - insets.bottom)}px`,
        fontFamily: node.font_family, fontSize: `${node.font_size}px`,
        lineHeight: `${node.line_height}px`, textAlign: node.align,
        letterSpacing: `${node.letter_spacing}px`,
        whiteSpace: node.wrap ? 'pre-wrap' : 'pre', overflowWrap: node.wrap ? 'anywhere' : 'normal',
        color: effectColor(node.fill), paintOrder: 'stroke fill'
      });
      if (stroke) layout.style.webkitTextStroke = `${stroke.width}px ${effectColor(stroke)}`;
      if (shadow) layout.style.textShadow = `${shadow.offset_x}px ${shadow.offset_y}px ${shadow.blur}px ${effectColor(shadow)}`;
      element.appendChild(layout);
    }
    stage.appendChild(element);
    elements.set(node.id, {node, element, layout, insets});
  }
  await document.fonts.ready;
  await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  const origin = stage.getBoundingClientRect();
  const measurements = new Map();
  const textMetrics = document.createElement('canvas').getContext('2d');
  for (const [id, entry] of elements) {
    const {node, element} = entry;
    if (node.kind === 'text') {
      measurements.set(id, fitText(entry, origin, textMetrics));
    } else {
      const box = relativeRect(element.getBoundingClientRect(), origin);
      const layoutBox = [node.x, node.y, node.width, node.height];
      const visibleBox = canvasVisibleBox(element.firstChild, box);
      measurements.set(id, {id, kind: node.kind, box, layout_box: layoutBox,
        content_box: layoutBox, visible_box: visibleBox, visible_box_source: 'canvas_alpha', overflow: false});
    }
  }
  alignBaselines(data.baseline_groups || [], elements, measurements, origin, textMetrics);
  for (const node of measurements.values()) {
    if (node.kind !== 'text') continue;
    node.visible_box = visibleTextBox(elements.get(node.id), node, origin, textMetrics);
    node.visible_box_source = 'estimated_text_ink';
  }
  const groups = data.layout_groups || [];
  const usages = data.component_usage || {};
  addOverlays(groups, measurements, usages);
  const warnings = layoutWarnings(groups, measurements, usages, data.checks || {});
  if (warnings.length) {
    const details = document.createElement('details');
    const summary = document.createElement('summary');
    summary.textContent = `${warnings.length} 项布局提示`;
    details.append(summary);
    const list = document.createElement('ul');
    for (const warning of warnings) {
      const item = document.createElement('li');
      const ids = warning.nodes?.map(node => node.id).join('、');
      const messages = {
        content_overlap: `节点 ${ids} 的内容范围重叠`,
        font_size_below_minimum: `文字 ${ids} 的实际字号 ${warning.font_size} 小于检查阈值 ${warning.min_font_size}`,
        missing_states: `组件 ${warning.component_id} 缺少状态：${warning.missing?.join('、')}`,
        stale_states: `组件 ${warning.component_id} 的状态需要从当前产物重新导出（${warning.stale?.join('、')}）`,
        visual_alignment_offset: `分组 ${warning.group_id} 内的节点 ${warning.node_id} 在 ${warning.axis} 轴偏移 ${warning.offset} px，超出 ${warning.tolerance} px 容差`,
        text_outside_component_rect: `文字 ${ids} 超出组件 ${warning.component_id} 的文字区域`
      };
      item.textContent = messages[warning.type];
      list.append(item);
    }
    details.append(list);
    document.getElementById('status').append(details);
  }
  zoom.disabled = false;
  return {fonts, nodes: [...measurements.values()], warnings};
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


def measure_text_widths(nodes):
    """使用真实字体和 Chromium 单行排版批量测量内容宽度，包含效果预留。"""
    from playwright.sync_api import Error, sync_playwright

    if not nodes:
        return {}
    fonts, items = {}, []
    for node in nodes:
        path = node["font_path"]
        if path not in fonts:
            fonts[path] = {"family": f"SmartUIMeasure_{len(fonts)}",
                           "url": _resource(path, node["font_sha256"], f"文字节点 {node['id']} 的字体", "font/otf")}
        items.append({**node, "font_family": fonts[path]["family"]})
    with sync_playwright() as playwright:
        browser = None
        try:
            browser = playwright.chromium.launch()
            context = browser.new_context()
            context.set_offline(True)
            tab = context.new_page()
            return tab.evaluate(r"""async ({fonts, nodes}) => {
              for (const font of fonts) {
                const face = new FontFace(font.family, `url(${font.url})`);
                await face.load();
                document.fonts.add(face);
              }
              const context = document.createElement('canvas').getContext('2d');
              const result = {};
              for (const node of nodes) {
                const span = document.createElement('span');
                span.textContent = node.text;
                Object.assign(span.style, {position:'absolute', whiteSpace:'pre',
                  fontFamily:node.font_family, fontSize:`${node.font_size}px`,
                  lineHeight:`${node.line_height}px`, letterSpacing:`${node.letter_spacing}px`,
                  fontSynthesis:'none'});
                document.body.appendChild(span);
                context.font = `${node.font_size}px ${node.font_family}`;
                context.letterSpacing = `${node.letter_spacing}px`;
                const metrics = context.measureText(node.text);
                const stroke = node.stroke?.opacity > 0 ? node.stroke.width / 2 : 0;
                const shadow = node.shadow?.opacity > 0 ? node.shadow : null;
                const spread = shadow && shadow.blur > 0 ? Math.ceil(2 * shadow.blur) + 1 : 0;
                const left = Math.max(stroke, shadow ? spread - shadow.offset_x : 0);
                const right = Math.max(stroke, shadow ? spread + shadow.offset_x : 0);
                const advance = span.getBoundingClientRect().width;
                // 字形实际绘制范围可能越过字距；给左右两端单独保留空间。
                const inkLeft = Math.ceil(Math.max(0, metrics.actualBoundingBoxLeft));
                const inkRight = Math.ceil(Math.max(0, metrics.actualBoundingBoxRight - advance));
                result[node.id] = {
                  width: Math.max(1, Math.ceil(advance + inkLeft + inkRight + left + right)),
                  content_insets: {left: inkLeft, right: inkRight}
                };
                span.remove();
              }
              return result;
            }""", {"fonts": list(fonts.values()), "nodes": items})
        except Error as error:
            raise ValueError(f"Chromium 测量文字内容宽度失败：{error}") from error
        finally:
            if browser is not None:
                browser.close()


def _html(page):
    fonts = {}
    nodes = []
    for node in page["nodes"]:
        item = dict(node)
        if node["kind"] == "image":
            item["url"] = _resource(node["source"], node["sha256"], f"图像节点 {node['id']}", "image/png")
        elif node["kind"] == "text":
            key = node["snapshot_key"]
            digest = node["font_sha256"]
            if key not in fonts:
                url = _resource(node["font_path"], digest, f"文字节点 {node['id']} 的字体", "font/otf")
                fonts[key] = {"family": f"SmartUIFont_{Path(key).stem}", "sha256": digest, "url": url}
            item["font_family"] = fonts[key]["family"]
        else:
            raise ValueError(f"浏览器预览不支持节点类型：{node['kind']}")
        # 独立 HTML 只携带实际内容，不保留本机资源路径。
        item.pop("source", None)
        item.pop("font_path", None)
        nodes.append(item)
    data = {"width": page["width"], "height": page["height"], "background": page["background"],
            "nodes": nodes, "fonts": list(fonts.values())}
    data.update({key: page[key] for key in ("layout_groups", "baseline_groups", "safe_area", "component_usage",
                                          "checks", "unity_adaptation") if key in page})
    payload = json.dumps(data, ensure_ascii=False, indent=2)
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
#toolbar { min-height: 48px; display: flex; flex-wrap: wrap; align-items: center; gap: 8px 16px; padding: 8px 16px; color: white; font: 14px sans-serif; }
#toolbar[hidden] { display: none; }
#status { position: relative; }
#status summary { cursor: pointer; }
#status ul { position: absolute; z-index: 2; top: 100%; right: 0; width: min(420px, 80vw); margin: 8px 0; padding: 12px 12px 12px 28px; background: #333; border: 1px solid #777; line-height: 1.6; }
#host { position: relative; margin: 16px; }
#stage { position: relative; overflow: hidden; transform-origin: 0 0; }
.node { position: absolute; margin: 0; padding: 0; border: 0; overflow: hidden; font-synthesis: none; }
.node > canvas { display: block; }
.baseline-marker { display: inline-block; width: 0; height: 0; vertical-align: baseline; }
#overlay { position: absolute; inset: 0; pointer-events: none; z-index: 1; }
.overlay-layer { position: absolute; inset: 0; }
.overlay-mark { position: absolute; }
.overlay-label { display: inline-block; background: #171d27; color: #fff; font: 11px sans-serif; }
.overlay-guides { color: #33d6ff; }
.overlay-content { color: #e6cf3d; }
.overlay-visible { color: #ff74e1; }
.overlay-baselines { color: #58f08a; }
.overlay-rect, .overlay-text-rect, .overlay-hit-rect { border: 1px dashed currentColor; }
.overlay-text-rect { color: #58f08a; }
.overlay-hit-rect { color: #ff9248; }
.overlay-line { border-top: 1px solid currentColor; }
.overlay-vertical-line { border-left: 1px solid currentColor; }
.overlay-pivot::before, .overlay-pivot::after { content: ''; position: absolute; background: currentColor; }
.overlay-pivot::before { left: -5px; width: 11px; height: 1px; }
.overlay-pivot::after { top: -5px; width: 1px; height: 11px; }
body.screenshot { background: transparent; }
body.screenshot #host { margin: 0; }
body.screenshot:not(.annotated) #overlay { display: none; }
</style></head><body>
<div id="toolbar"><span>SmartUI 页面预览</span><label>缩放 <select id="zoom" disabled><option value="0.25">25%</option><option value="0.5">50%</option><option value="1" selected>100%</option><option value="1.5">150%</option><option value="2">200%</option></select></label>
<label><input type="checkbox" data-layer="guides"> 辅助线</label>
<label><input type="checkbox" data-layer="content"> 内容框</label>
<label title="图像使用 Alpha 像素范围。文字根据可见行的字形与效果估计边界。"><input type="checkbox" data-layer="visible"> 可见边界</label>
<label><input type="checkbox" data-layer="baselines"> 文字基线</label><span id="status"></span></div>
<div id="host"><div id="stage"><div id="overlay" aria-hidden="true"></div></div></div>
<script type="application/json" id="page-data">""" + payload + "</script>\n<script>" + _SCRIPT + "</script></body></html>\n"


def _capture(html_path, png_path, width, height, annotation_path):
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
            if annotation_path is not None:
                tab.evaluate("""() => {
                  document.body.classList.add('annotated');
                  for (const input of document.querySelectorAll('[data-layer="guides"], [data-layer="content"]')) {
                    input.checked = true;
                    input.dispatchEvent(new Event('change'));
                  }
                }""")
                tab.screenshot(path=str(annotation_path), type="png", omit_background=True, animations="disabled")
            return {"engine": "chromium", "version": browser.version, "viewport": {"width": width, "height": height},
                    **environment, **evidence}
        except Error as error:
            raise ValueError(f"浏览器页面渲染失败：{error}") from error
        finally:
            browser.close()


def render_page(directory: Path, page: dict, *, annotate=False) -> dict:
    """保存可离线打开的 HTML，以同一页面输出参考尺寸的 PNG 和渲染证据。"""
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    html_path = directory / "preview.html"
    png_path = directory / "overview.png"
    html_path.write_text(_html(page), encoding="utf-8", newline="\n")
    annotation_path = directory / "annotated.png" if annotate else None
    renderer = _capture(html_path, png_path, page["width"], page["height"], annotation_path)
    result = {"html": {"path": str(html_path), "sha256": _sha(html_path.read_bytes())},
            "overview": {"path": str(png_path), "sha256": _sha(png_path.read_bytes()),
                         "size": [page["width"], page["height"]]}, "renderer": renderer}
    if annotation_path is not None:
        result["annotated"] = {"path": str(annotation_path), "sha256": _sha(annotation_path.read_bytes()),
                               "size": [page["width"], page["height"]]}
    return result
