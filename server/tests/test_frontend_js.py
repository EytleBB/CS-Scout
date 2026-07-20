import json
import os
import shutil
import subprocess

import pytest


NODE = shutil.which("node")
REPLAY_JS = os.path.join(os.path.dirname(__file__), "..", "static", "replay.js")
APP_JS = os.path.join(os.path.dirname(__file__), "..", "static", "app.js")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")


def test_replay_player_runtime_contract():
    script = f"""
global.Image = class FakeImage {{
  constructor() {{
    this.complete = false;
    this.naturalWidth = 0;
    this.naturalHeight = 0;
  }}
  set src(value) {{ this._src = value; }}
}};

const {{ ReplayPlayer, NADE_ICON_SRC, NADE_EFFECT_SRC }} = require({json.dumps(os.path.abspath(REPLAY_JS))});
const calls = {{ lineTo: [], fillText: [] }};
const ctx = {{
  clearRect() {{}}, fillRect() {{}}, drawImage() {{}}, save() {{}}, restore() {{}},
  beginPath() {{}}, moveTo() {{}}, stroke() {{}}, arc() {{}}, fill() {{}},
  translate() {{}}, rotate() {{}}, closePath() {{}},
  lineTo(x, y) {{ calls.lineTo.push([x, y]); }},
  fillText(text, x, y) {{ calls.fillText.push([text, x, y]); }}
}};
const canvas = {{ width: 300, height: 150, getContext() {{ return ctx; }} }};
const rounds = [];
const player = new ReplayPlayer(canvas, {{
  radar: "/maps/de_test/radar.png",
  transform: {{ pos_x: 0, pos_y: 0, scale: 1 }},
  rounds,
  side: "CT",
  rtype: "Pistol"
}});

if (NADE_ICON_SRC.smoke !== "smokegrenade.svg" ||
    NADE_ICON_SRC.flash !== "flashbang.svg" ||
    NADE_ICON_SRC.he !== "hegrenade.svg") throw new Error("new flying icons are not mapped");
if (NADE_EFFECT_SRC.smoke !== "map_smoke.svg" ||
    NADE_EFFECT_SRC.molotov !== "inferno.svg") throw new Error("landing effects are not mapped");
if (player._nadeIconSource("molotov") !== "incgrenade.svg") throw new Error("CT incendiary icon missing");
player.setFilter("T", "Pistol");
if (player._nadeIconSource("molotov") !== "molotov_bottle.svg") throw new Error("T molotov icon missing");
player.setFilter("CT", "Pistol");

if (player._interp([[5, 10, 20]], 4) !== null) throw new Error("path appeared before first sample");
if (JSON.stringify(player._interp([[5, 10, 20]], 5)) !== "[10,20]") throw new Error("exact sample missing");
if (player._interp([[5, 10, 20]], 6) !== null) throw new Error("live path persisted after last sample");
if (JSON.stringify(player._interp([[5, 10, 20]], 6, true)) !== "[10,20]") throw new Error("held marker missing");

rounds.push({{ side: "CT", rtype: "Pistol", round_id: 1, path: [], grenades: [] }});
if (player._rounds().length !== 1) throw new Error("mutable merged-round reference was lost");
player.setFilter("T", "Pistol");
if (player._rounds().length !== 0) throw new Error("side filter did not update");

player._drawGrenade({{
  type: "smoke", throw_t: 1, land_t: 3, expire_t: 20,
  arc: [[1, 0, 0], [3, 10, 0]], land: [10, 0]
}}, 2);
const lastLine = calls.lineTo[calls.lineTo.length - 1];
if (JSON.stringify(lastLine) !== "[5,0]") throw new Error("airborne arc did not reach interpolated icon head");

player.imgFailed = true;
player.drawAt(0);
if (!calls.fillText.some(call => call[0] === "雷达图加载失败")) throw new Error("radar failure was not visible");
"""
    result = subprocess.run(
        [NODE, "-e", script],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_playback_speed_math_preserves_default_two_x():
    script = f"""
const {{ playbackElapsedDelta }} = require({json.dumps(os.path.abspath(APP_JS))});
const values = [1, 2, 4].map(speed => playbackElapsedDelta(1, speed));
if (JSON.stringify(values) !== "[0.5,1,2]") {{
  throw new Error(`unexpected elapsed deltas: ${{JSON.stringify(values)}}`);
}}
if (playbackElapsedDelta(1, 3) !== 0 || playbackElapsedDelta(-1, 2) !== 0) {{
  throw new Error("invalid playback speed input was accepted");
}}
"""
    result = subprocess.run(
        [NODE, "-e", script],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_speed_buttons_wire_to_clock_rate():
    script = f"""
const {{ playbackElapsedDelta, wireControls }} = require({json.dumps(os.path.abspath(APP_JS))});

function speedButton(rate) {{
  const classes = new Set(rate === 2 ? ["active"] : []);
  return {{
    dataset: {{ playbackSpeed: String(rate) }},
    attributes: {{}},
    listeners: {{}},
    classList: {{
      toggle(name, enabled) {{ if (enabled) classes.add(name); else classes.delete(name); }},
      contains(name) {{ return classes.has(name); }}
    }},
    setAttribute(name, value) {{ this.attributes[name] = String(value); }},
    addEventListener(name, handler) {{ this.listeners[name] = handler; }}
  }};
}}

const buttons = [1, 2, 4].map(speedButton);
global.document = {{
  activeElement: null,
  querySelector() {{ return null; }},
  querySelectorAll(selector) {{ return selector === "[data-playback-speed]" ? buttons : []; }},
  addEventListener() {{}}
}};

wireControls();
if (buttons.some(button => typeof button.listeners.click !== "function")) {{
  throw new Error("a speed button has no click handler");
}}
if (!buttons[1].classList.contains("active") || buttons[1].attributes["aria-pressed"] !== "true") {{
  throw new Error("2x was not initialized as the active speed");
}}
buttons[0].listeners.click();
if (playbackElapsedDelta(1) !== 0.5 || !buttons[0].classList.contains("active") ||
    buttons[1].classList.contains("active") || buttons[0].attributes["aria-pressed"] !== "true") {{
  throw new Error("1x click did not update the clock and pressed state");
}}
buttons[2].listeners.click();
if (playbackElapsedDelta(1) !== 2 || !buttons[2].classList.contains("active")) {{
  throw new Error("4x click did not update the clock rate");
}}
"""
    result = subprocess.run(
        [NODE, "-e", script],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_analysis_mode_buttons_default_switch_and_lock_while_running():
    script = f"""
const {{ wireControls, setAnalysisBusy }} = require({json.dumps(os.path.abspath(APP_JS))});

function modeButton(mode) {{
  const classes = new Set(mode === "normal" ? ["active"] : []);
  return {{
    dataset: {{ analysisMode: mode }}, disabled: false,
    attributes: {{}}, listeners: {{}},
    classList: {{
      toggle(name, enabled) {{ if (enabled) classes.add(name); else classes.delete(name); }},
      contains(name) {{ return classes.has(name); }}
    }},
    setAttribute(name, value) {{ this.attributes[name] = String(value); }},
    addEventListener(name, handler) {{ this.listeners[name] = handler; }}
  }};
}}

const modes = [modeButton("normal"), modeButton("fast")];
const run = {{ disabled: false }};
global.document = {{
  activeElement: null,
  querySelector(selector) {{ return selector === "#run" ? run : null; }},
  querySelectorAll(selector) {{
    if (selector === "[data-analysis-mode]") return modes;
    if (selector === "[data-playback-speed]") return [];
    return [];
  }},
  addEventListener() {{}}
}};

wireControls();
if (!modes[0].classList.contains("active") ||
    modes[0].attributes["aria-pressed"] !== "true" ||
    modes[1].attributes["aria-pressed"] !== "false") {{
  throw new Error("normal mode was not the accessible default");
}}
modes[1].listeners.click();
if (!modes[1].classList.contains("active") ||
    modes[0].classList.contains("active") ||
    modes[1].attributes["aria-pressed"] !== "true") {{
  throw new Error("fast mode click did not switch the pressed state");
}}
setAnalysisBusy(true);
if (!run.disabled || modes.some(button => !button.disabled)) {{
  throw new Error("analysis controls stayed enabled while running");
}}
setAnalysisBusy(false);
if (run.disabled || modes.some(button => button.disabled)) {{
  throw new Error("analysis controls did not unlock after completion");
}}
"""
    result = subprocess.run(
        [NODE, "-e", script],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_409_recovery_clears_stale_results_before_polling_other_tab():
    script = f"""
const {{ runAnalysis }} = require({json.dumps(os.path.abspath(APP_JS))});

function element(overrides = {{}}) {{
  const classes = new Set();
  return Object.assign({{
    value: "", disabled: false, hidden: false, children: [], textContent: "",
    attributes: {{}}, dataset: {{}},
    classList: {{
      toggle(name, enabled) {{ if (enabled) classes.add(name); else classes.delete(name); }}
    }},
    setAttribute(name, value) {{ this.attributes[name] = String(value); }},
    appendChild(child) {{ this.children.push(child); }},
    replaceChildren(...children) {{ this.children = children; }},
  }}, overrides);
}}

const elements = {{
  "#map": element({{ value: "de_mirage" }}),
  "#depth": element({{ value: "2" }}),
  "#key": element({{ value: "test-key" }}),
  "#run": element(),
  "#u0": element({{ value: "Alpha" }}),
  "#u1": element(), "#u2": element(), "#u3": element(), "#u4": element(),
  "#status": element(), "#failed": element(),
  "#cards": element({{ children: [{{ stale: true }}] }}),
  "#view-switcher": element({{ children: [{{ stale: true }}] }}),
  "#view-toolbar": element(), "#pistol-legend": element({{ children: [{{ stale: true }}] }}),
  "#pistol": element(), "#empty-state": element({{ hidden: true }}),
  "#side-ct": element(), "#side-t": element(),
}};
const modeButtons = [element({{ dataset: {{ analysisMode: "normal" }} }}),
                     element({{ dataset: {{ analysisMode: "fast" }} }})];
global.document = {{
  activeElement: null,
  querySelector(selector) {{ return elements[selector] || null; }},
  querySelectorAll(selector) {{
    return selector === "[data-analysis-mode]" ? modeButtons : [];
  }},
  createElement() {{ return element(); }},
}};

const requests = [];
global.fetch = async (url, options) => {{
  requests.push([url, options]);
  if (url === "/api/analyze") return {{
    ok: false, status: 409,
    async json() {{ return {{ error: "Analysis already running" }}; }}
  }};
  if (url === "/api/status") return {{
    ok: true, status: 200,
    async json() {{ return {{ status: "idle", message: "idle", results: [], failed: [] }}; }}
  }};
  throw new Error(`unexpected URL: ${{url}}`);
}};

(async () => {{
  await runAnalysis();
  if (requests.length !== 2 || requests[0][0] !== "/api/analyze" ||
      requests[1][0] !== "/api/status") {{
    throw new Error(`409 recovery did not resume polling: ${{JSON.stringify(requests)}}`);
  }}
  if (requests.some(([, options]) =>
      !options || options.headers.Authorization !== "Bearer test-key")) {{
    throw new Error(`protected request missed Bearer key: ${{JSON.stringify(requests)}}`);
  }}
  const analyzeBody = JSON.parse(requests[0][1].body);
  if (Object.prototype.hasOwnProperty.call(analyzeBody, "key")) {{
    throw new Error("access key was duplicated into the analysis JSON body");
  }}
  if (elements["#cards"].children.length !== 0 ||
      elements["#view-switcher"].children.length !== 0 ||
      elements["#pistol-legend"].children.length !== 0) {{
    throw new Error("stale replay results survived 409 recovery");
  }}
  if (!elements["#view-switcher"].hidden || !elements["#view-toolbar"].hidden ||
      !elements["#pistol"].hidden || elements["#empty-state"].hidden) {{
    throw new Error("empty-state visibility was not restored during 409 recovery");
  }}
  if (elements["#run"].disabled || modeButtons.some(button => button.disabled)) {{
    throw new Error("controls stayed locked after recovered task was already idle");
  }}
}})().catch(error => {{ console.error(error); process.exitCode = 1; }});
"""
    result = subprocess.run(
        [NODE, "-e", script],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_frontend_does_not_request_sensitive_routes_without_key():
    script = f"""
const {{ runAnalysis }} = require({json.dumps(os.path.abspath(APP_JS))});

function element(overrides = {{}}) {{
  return Object.assign({{
    value: "", disabled: false, textContent: "", children: [],
    replaceChildren(...children) {{ this.children = children; }}
  }}, overrides);
}}

const elements = {{
  "#map": element({{ value: "de_mirage" }}),
  "#depth": element({{ value: "2" }}),
  "#key": element({{ value: "" }}),
  "#run": element(),
  "#u0": element({{ value: "Alpha" }}),
  "#u1": element(), "#u2": element(), "#u3": element(), "#u4": element(),
  "#status": element()
}};
global.document = {{
  querySelector(selector) {{ return elements[selector] || null; }},
  querySelectorAll() {{ return []; }}
}};

let fetchCount = 0;
global.fetch = async () => {{ fetchCount += 1; throw new Error("fetch must not run"); }};

(async () => {{
  await runAnalysis();
  if (fetchCount !== 0) throw new Error("a protected endpoint was called without a key");
  if (!elements["#status"].textContent.includes("请输入访问密钥")) {{
    throw new Error(`missing-key guidance was not shown: ${{elements["#status"].textContent}}`);
  }}
  if (elements["#run"].disabled) throw new Error("run button stayed disabled");
}})().catch(error => {{ console.error(error); process.exitCode = 1; }});
"""
    result = subprocess.run(
        [NODE, "-e", script],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_entered_key_connects_to_existing_analysis_with_bearer_auth():
    script = f"""
const {{ connectWithEnteredKey }} = require({json.dumps(os.path.abspath(APP_JS))});

function element(overrides = {{}}) {{
  return Object.assign({{
    value: "", disabled: false, textContent: "", children: [],
    replaceChildren(...children) {{ this.children = children; }}
  }}, overrides);
}}

const elements = {{
  "#key": element({{ value: "shared-secret" }}),
  "#status": element(), "#run": element(), "#failed": element()
}};
global.document = {{
  querySelector(selector) {{ return elements[selector] || null; }},
  querySelectorAll() {{ return []; }}
}};

const requests = [];
global.fetch = async (url, options) => {{
  requests.push([url, options]);
  return {{
    ok: true, status: 200,
    async json() {{ return {{ status: "idle", message: "idle", results: [], failed: [] }}; }}
  }};
}};

(async () => {{
  await connectWithEnteredKey();
  if (requests.length !== 1 || requests[0][0] !== "/api/status") {{
    throw new Error(`key entry did not read status: ${{JSON.stringify(requests)}}`);
  }}
  if (requests[0][1].headers.Authorization !== "Bearer shared-secret") {{
    throw new Error("status request did not carry the entered key");
  }}
  if (elements["#status"].textContent !== "idle") {{
    throw new Error(`existing status was not shown: ${{elements["#status"].textContent}}`);
  }}
}})().catch(error => {{ console.error(error); process.exitCode = 1; }});
"""
    result = subprocess.run(
        [NODE, "-e", script],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_app_button_views_keep_one_panel_active_and_draw_only_it():
    script = f"""
const {{ registerReplayView, drawAll }} = require({json.dumps(os.path.abspath(APP_JS))});

function element(id = "") {{
  const classes = new Set();
  return {{
    id, hidden: false, children: [], dataset: {{}}, attributes: {{}}, listeners: {{}},
    classList: {{
      toggle(name, enabled) {{ if (enabled) classes.add(name); else classes.delete(name); }},
      contains(name) {{ return classes.has(name); }}
    }},
    style: {{ setProperty(name, value) {{ this[name] = value; }} }},
    appendChild(child) {{ this.children.push(child); }},
    setAttribute(name, value) {{ this.attributes[name] = String(value); }},
    addEventListener(name, handler) {{ this.listeners[name] = handler; }}
  }};
}}

const switcher = element("view-switcher");
const toolbar = element("view-toolbar");
toolbar.hidden = true;
const empty = element("empty-state");
global.document = {{
  querySelector(selector) {{
    if (selector === "#view-switcher") return switcher;
    if (selector === "#view-toolbar") return toolbar;
    if (selector === "#empty-state") return empty;
    return null;
  }},
  createElement() {{ return element(); }}
}};

const panels = Array.from({{length: 6}}, (_, index) =>
  element(index === 0 ? "pistol" : `buy-${{index}}`));
const draws = [0, 0, 0, 0, 0, 0];
const players = draws.map((_, index) => ({{ drawAt() {{ draws[index] += 1; }} }}));

registerReplayView("pistol", "手枪局（全员）", panels[0], players[0], "#5d86ff");
registerReplayView("buy:one", "一号", panels[1], players[1], "#ef6aa8", "一号 购买局");
if (switcher.children.length !== 2) throw new Error("initial buttons were not registered");
if (switcher.children[1].textContent !== "一号" ||
    switcher.children[1].attributes["aria-label"] !== "一号 购买局") {{
  throw new Error("Buy suffix was not limited to the accessible label");
}}
if (panels[0].hidden || !panels[1].hidden) throw new Error("pistol was not the initial view");

switcher.children[1].listeners.click();
if (!panels[0].hidden || panels[1].hidden) throw new Error("Buy view did not activate");
if (switcher.children[0].attributes["aria-pressed"] !== "false" ||
    switcher.children[1].attributes["aria-pressed"] !== "true") {{
  throw new Error("button pressed state is inconsistent");
}}

for (let index = 2; index <= 5; index += 1) {{
  registerReplayView(`buy:${{index}}`, `${{index}}号`, panels[index], players[index], "#55c8ff");
}}
if (switcher.children.length !== 6) throw new Error("five player buttons were not appended");
if (panels[1].hidden || panels.slice(2).some(panel => !panel.hidden)) {{
  throw new Error("later player stole the active view");
}}
registerReplayView("buy:5", "重复", panels[5], players[5]);
if (switcher.children.length !== 6) throw new Error("duplicate domain created another button");

const before = [...draws];
drawAll(7);
if (draws[0] !== before[0] || draws[1] !== before[1] + 1 ||
    draws.slice(2).some((value, index) => value !== before[index + 2])) {{
  throw new Error("hidden replay players were still drawn");
}}
if (!empty.hidden || switcher.hidden || toolbar.hidden) throw new Error("result navigation visibility is wrong");
"""
    result = subprocess.run(
        [NODE, "-e", script],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
