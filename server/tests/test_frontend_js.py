import json
import os
import shutil
import subprocess

import pytest


NODE = shutil.which("node")
REPLAY_JS = os.path.join(os.path.dirname(__file__), "..", "static", "replay.js")
PREP_JS = os.path.join(os.path.dirname(__file__), "..", "static", "prep.js")
APP_JS = os.path.join(os.path.dirname(__file__), "..", "static", "app.js")
INDEX_HTML = os.path.join(os.path.dirname(__file__), "..", "templates", "index.html")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")


def test_fivee_non_default_install_has_visible_graphical_picker():
    with open(APP_JS, encoding="utf-8") as source:
        app_source = source.read()
    with open(INDEX_HTML, encoding="utf-8") as source:
        html_source = source.read()

    assert 'id="fivee-select-executable"' in html_source
    assert "/api/5e/exe/select" in app_source
    assert 'headers: { "X-CS-Scout-Request": "1" }' in app_source


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
const platforms = [modeButton("normal"), modeButton("fast")];
platforms[0].dataset = {{ platform: "5e" }};
platforms[1].dataset = {{ platform: "perfectworld" }};
const run = {{ disabled: false }};
global.document = {{
  activeElement: null,
  querySelector(selector) {{ return selector === "#run" ? run : null; }},
  querySelectorAll(selector) {{
    if (selector === "[data-analysis-mode]") return modes;
    if (selector === "[data-platform]") return platforms;
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
if (!run.disabled || modes.some(button => !button.disabled) ||
    platforms.some(button => !button.disabled)) {{
  throw new Error("analysis controls stayed enabled while running");
}}
setAnalysisBusy(false);
if (run.disabled || modes.some(button => button.disabled) ||
    platforms.some(button => button.disabled)) {{
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


def test_local_scout_mode_switches_between_readonly_auto_and_saved_manual_names():
    script = f"""
const {{ setScoutMode }} = require({json.dumps(os.path.abspath(APP_JS))});

function classes(initial = []) {{
  const values = new Set(initial);
  return {{
    toggle(name, enabled) {{ if (enabled) values.add(name); else values.delete(name); }},
    contains(name) {{ return values.has(name); }}
  }};
}}
function button(mode) {{
  return {{
    dataset: {{ scoutMode: mode }}, disabled: false,
    classList: classes(mode === "auto" ? ["active"] : []),
    attributes: {{}},
    setAttribute(name, value) {{ this.attributes[name] = String(value); }},
    addEventListener() {{}}
  }};
}}
function input(value = "") {{
  return {{
    value, readOnly: true, disabled: false, placeholder: "", attributes: {{}},
    setAttribute(name, value) {{ this.attributes[name] = String(value); }},
    addEventListener() {{}}
  }};
}}

const scoutModes = [button("auto"), button("manual")];
const elements = {{
  "#run": {{ disabled: true, textContent: "", classList: classes() }},
  "#map": {{ value: "de_mirage", disabled: true }},
  "#depth": input("2"),
  "#status": {{ textContent: "" }},
  "#u0": input(), "#u1": input(), "#u2": input(), "#u3": input(), "#u4": input()
}};
global.document = {{
  body: {{ dataset: {{ localAnalysis: "true" }} }},
  activeElement: null,
  querySelector(selector) {{ return elements[selector] || null; }},
  querySelectorAll(selector) {{
    if (selector === "[data-scout-mode]") return scoutModes;
    return [];
  }},
  addEventListener() {{}}
}};
global.fetch = async (url, options = {{}}) => {{
  if (url === "/api/5e/config") return {{
    ok: true, status: 200,
    async json() {{ return {{ max_demos: 2, mode: "normal" }}; }}
  }};
  if (url === "/api/5e/status") return {{
    ok: true, status: 200,
    async json() {{ return {{
      platform: "fivee", phase: "waiting", message: "等待对局",
      manual_fallback: false, needs_map: false, mode: "normal",
      targets: [], team_options: [], analysis_busy: false
    }}; }}
  }};
  throw new Error(`unexpected URL: ${{url}}`);
}};

(async () => {{
  await setScoutMode("manual");
  if (elements["#u0"].readOnly || elements["#run"].disabled) {{
    throw new Error("manual mode did not unlock inputs and analysis");
  }}
  if (!scoutModes[1].classList.contains("active") ||
      scoutModes[1].attributes["aria-pressed"] !== "true") {{
    throw new Error("manual mode button did not become active");
  }}

  elements["#u0"].value = "Alpha";
  await setScoutMode("auto");
  if (!elements["#u0"].readOnly || !elements["#map"].disabled ||
      !scoutModes[0].classList.contains("active")) {{
    throw new Error("automatic mode did not restore readonly controls");
  }}

  await setScoutMode("manual");
  if (elements["#u0"].value !== "Alpha") {{
    throw new Error("manual username was not restored after switching modes");
  }}
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
    result = subprocess.run(
        [NODE, "-e", script], capture_output=True, text=True, timeout=15, check=False
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_running_button_cancels_and_unlocks_for_retry():
    script = f"""
const {{ setAnalysisBusy, runAnalysis }} = require({json.dumps(os.path.abspath(APP_JS))});

function element() {{
  const classes = new Set();
  return {{
    value: "", disabled: false, hidden: false, textContent: "",
    dataset: {{}}, attributes: {{}}, children: [],
    classList: {{
      toggle(name, enabled) {{ if (enabled) classes.add(name); else classes.delete(name); }},
      contains(name) {{ return classes.has(name); }}
    }},
    setAttribute(name, value) {{ this.attributes[name] = String(value); }},
    replaceChildren(...children) {{ this.children = children; }}
  }};
}}

const run = element();
const status = element();
global.document = {{
  body: {{ dataset: {{ localAnalysis: "true" }} }},
  querySelector(selector) {{
    if (selector === "#run") return run;
    if (selector === "#status") return status;
    return null;
  }},
  querySelectorAll() {{ return []; }},
  createElement() {{ return element(); }}
}};

let releaseCancel;
const requests = [];
global.fetch = async (url) => {{
  requests.push(url);
  if (url === "/api/cancel") {{
    await new Promise(resolve => {{ releaseCancel = resolve; }});
    return {{ ok: true, status: 202, async json() {{ return {{ status: "cancelling" }}; }} }};
  }}
  if (url === "/api/5e/status") return {{
    ok: true, status: 200,
    async json() {{ return {{
      platform: "fivee", phase: "awaiting_confirmation",
      message: "分析已取消，可重新开始", analysis_busy: false,
      targets: [], team_options: []
    }}; }}
  }};
  throw new Error(`unexpected URL: ${{url}}`);
}};

(async () => {{
  setAnalysisBusy(true, {{ cancellable: true }});
  if (run.disabled || run.textContent !== "取消分析" ||
      !run.classList.contains("cancel-action")) {{
    throw new Error("running task did not expose the cancel action");
  }}
  const task = runAnalysis();
  await new Promise(resolve => setImmediate(resolve));
  if (!run.disabled || run.textContent !== "正在取消…") {{
    throw new Error("cancel request did not enter a protected cancelling state");
  }}
  releaseCancel();
  await task;
  if (JSON.stringify(requests) !== JSON.stringify(["/api/cancel", "/api/5e/status"])) {{
    throw new Error(`wrong cancel requests: ${{JSON.stringify(requests)}}`);
  }}
  if (run.disabled || run.textContent !== "开始分析" ||
      run.classList.contains("cancel-action")) {{
    throw new Error("cancelled task did not unlock for retry");
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


def test_local_fivee_mode_click_is_saved_and_not_reverted_by_stale_status():
    script = f"""
const {{ wireControls }} = require({json.dumps(os.path.abspath(APP_JS))});

function button(mode) {{
  const classes = new Set(mode === "normal" ? ["active"] : []);
  return {{
    dataset: {{ analysisMode: mode }}, disabled: false, listeners: {{}}, attributes: {{}},
    classList: {{
      toggle(name, enabled) {{ if (enabled) classes.add(name); else classes.delete(name); }},
      contains(name) {{ return classes.has(name); }}
    }},
    setAttribute(name, value) {{ this.attributes[name] = String(value); }},
    addEventListener(name, handler) {{ this.listeners[name] = handler; }}
  }};
}}

const modes = [button("normal"), button("fast")];
const depth = {{ value: "2", disabled: false }};
const run = {{ disabled: false }};
global.document = {{
  body: {{ dataset: {{ localAnalysis: "true" }} }},
  activeElement: null,
  querySelector(selector) {{
    if (selector === "#depth") return depth;
    if (selector === "#run") return run;
    return null;
  }},
  querySelectorAll(selector) {{
    if (selector === "[data-analysis-mode]") return modes;
    if (selector === "[data-playback-speed]" || selector === "[data-platform]" ||
        selector === "#fivee-team-choice button") return [];
    return [];
  }},
  addEventListener() {{}}
}};

let releaseConfig;
const requests = [];
global.fetch = async (url, options) => {{
  requests.push([url, options]);
  if (url !== "/api/5e/config") throw new Error(`unexpected URL: ${{url}}`);
  await new Promise(resolve => {{ releaseConfig = resolve; }});
  return {{ ok: true, status: 200, async json() {{ return {{ mode: "fast", max_demos: 2 }}; }} }};
}};

(async () => {{
  wireControls();
  modes[1].listeners.click();
  await new Promise(resolve => setImmediate(resolve));
  if (!modes[1].classList.contains("active") || !modes[0].disabled || !modes[1].disabled) {{
    throw new Error("fast mode was not held while the server configuration was pending");
  }}
  if (requests.length !== 1) throw new Error(`mode click sent ${{requests.length}} requests`);
  const body = JSON.parse(requests[0][1].body);
  if (body.mode !== "fast" || body.max_demos !== 2) {{
    throw new Error(`wrong 5E configuration: ${{requests[0][1].body}}`);
  }}
  releaseConfig();
  await new Promise(resolve => setImmediate(resolve));
  if (!modes[1].classList.contains("active") || modes[0].classList.contains("active") ||
      modes.some(item => item.disabled)) {{
    throw new Error("confirmed fast mode did not stay selected and unlock");
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


def test_progress_card_calculates_parallel_progress_and_collapses_failures():
    script = f"""
const {{ renderAnalysisProgress }} = require({json.dumps(os.path.abspath(APP_JS))});

function element() {{
  return {{
    textContent: "", hidden: false, open: false, dataset: {{}},
    style: {{}}, attributes: {{}}, children: [],
    setAttribute(name, value) {{ this.attributes[name] = String(value); }},
    replaceChildren(...children) {{ this.children = children; }}
  }};
}}
const elements = {{
  "#progress-panel": element(), "#progress-track": element(),
  "#progress-fill": element(), "#progress-count": element(),
  "#status": element()
}};
global.document = {{ querySelector(selector) {{ return elements[selector] || null; }} }};

renderAnalysisProgress({{
  status: "running", total_players: 5, message: "running",
  progress: [
    {{ id: "Alpha", step: 5, msg: "生成回放数据...", updated_at: 1 }},
    {{ id: "Bravo", step: 3, msg: "下载 demo 2/4...", updated_at: 2 }}
  ],
  results: [], failed: []
}}, true);

if (elements["#progress-fill"].style.width !== "28%" ||
    elements["#progress-count"].textContent !== "0/5 · 28%") {{
  throw new Error(`wrong overall progress: ${{JSON.stringify(elements)}}`);
}}
if (!elements["#status"].textContent.startsWith("Bravo · 下载 demo 2/4")) {{
  throw new Error(`latest step was not shown: ${{elements["#status"].textContent}}`);
}}
if (elements["#progress-track"].attributes["aria-valuenow"] !== "28" ||
    elements["#progress-panel"].dataset.state !== "running") {{
  throw new Error("progress accessibility/state was not updated");
}}

renderAnalysisProgress({{
  status: "running", total_players: 1, message: "running",
  progress: [
    {{ id: "Alpha", step: 3, msg: "下载 demo 0/6 · 50%...", updated_at: 3 }}
  ],
  results: [], failed: []
}}, true);
if (elements["#progress-fill"].style.width !== "43%") {{
  throw new Error(`byte progress was ignored: ${{elements["#progress-fill"].style.width}}`);
}}

renderAnalysisProgress({{
  status: "running", total_players: 2, message: "running",
  progress: [
    {{ id: "Missing", step: 6, msg: "5E 上未找到该玩家", updated_at: 4 }},
    {{ id: "Alpha", step: 3, msg: "下载 demo 0/6 · 20%...", updated_at: 5 }}
  ],
  results: [], failed: []
}}, true);
if (!elements["#progress-count"].textContent.startsWith("1/2 ·")) {{
  throw new Error(`finished failure was not counted: ${{elements["#progress-count"].textContent}}`);
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
  if (!requests[0][1] || requests[0][1].headers.Authorization !== "Bearer test-key") {{
    throw new Error(`analysis request missed Bearer key: ${{JSON.stringify(requests)}}`);
  }}
  if (requests[1][1] && requests[1][1].headers &&
      requests[1][1].headers.Authorization) {{
    throw new Error(`public status request carried a key: ${{JSON.stringify(requests)}}`);
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


def test_frontend_does_not_start_analysis_without_key():
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


def test_local_frontend_confirms_detected_fivee_match_without_access_key():
    script = f"""
const {{ runAnalysis }} = require({json.dumps(os.path.abspath(APP_JS))});

function element(overrides = {{}}) {{
  return Object.assign({{
    value: "", disabled: false, hidden: false, textContent: "", children: [],
    attributes: {{}}, classList: {{ toggle() {{}} }},
    setAttribute(name, value) {{ this.attributes[name] = String(value); }},
    replaceChildren(...children) {{ this.children = children; }}
  }}, overrides);
}}

const elements = {{
  "#map": element({{ value: "de_mirage" }}),
  "#depth": element({{ value: "1" }}),
  "#run": element(),
  "#u0": element({{ value: "Alpha" }}),
  "#u1": element(), "#u2": element(), "#u3": element(), "#u4": element(),
  "#status": element(), "#failed": element()
}};
global.document = {{
  body: {{ dataset: {{ localAnalysis: "true" }} }},
  querySelector(selector) {{ return elements[selector] || null; }},
  querySelectorAll() {{ return []; }},
  createElement() {{ return element(); }}
}};

const requests = [];
global.fetch = async (url, options) => {{
  requests.push([url, options]);
  if (url === "/api/5e/config") return {{
    ok: true, status: 200, async json() {{ return {{ max_demos: 1, mode: "normal" }}; }}
  }};
  if (url === "/api/5e/analyze") return {{
    ok: true, status: 200, async json() {{ return {{ status: "started" }}; }}
  }};
  if (url === "/api/5e/status") return {{
    ok: true, status: 200,
    async json() {{ return {{
      platform: "fivee", phase: "ready", message: "done",
      analysis_busy: false, targets: [], team_options: [],
      analysis: {{ status: "done", message: "done", results: [], failed: [] }}
    }}; }}
  }};
  throw new Error(`unexpected URL: ${{url}}`);
}};

(async () => {{
  await runAnalysis();
  if (requests.length !== 3 || requests[0][0] !== "/api/5e/config" ||
      requests[1][0] !== "/api/5e/analyze" || requests[2][0] !== "/api/5e/status") {{
    throw new Error(`automatic 5E analysis did not start: ${{JSON.stringify(requests)}}`);
  }}
  const headers = requests[1][1] && requests[1][1].headers;
  if (headers && headers.Authorization) {{
    throw new Error("local analysis unexpectedly sent an Authorization header");
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


def test_entered_key_refreshes_public_analysis_without_sending_key():
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
  if (requests[0][1] && requests[0][1].headers &&
      requests[0][1].headers.Authorization) {{
    throw new Error("public status request unexpectedly carried the entered key");
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


def test_app_button_views_reset_and_replay_when_switching_players():
    script = f"""
const {{ registerReplayView, drawAll, wireControls }} = require({json.dumps(os.path.abspath(APP_JS))});

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
const scrub = element("scrub");
scrub.value = "0";
const timeLabel = element("timelbl");
const playPause = element("playpause");
global.document = {{
  activeElement: null,
  body: {{ dataset: {{ localAnalysis: "false" }} }},
  querySelector(selector) {{
    if (selector === "#view-switcher") return switcher;
    if (selector === "#view-toolbar") return toolbar;
    if (selector === "#empty-state") return empty;
    if (selector === "#scrub") return scrub;
    if (selector === "#timelbl") return timeLabel;
    if (selector === "#playpause") return playPause;
    return null;
  }},
  querySelectorAll() {{ return []; }},
  addEventListener() {{}},
  createElement() {{ return element(); }}
}};

const panels = Array.from({{length: 6}}, (_, index) =>
  element(index === 0 ? "pistol" : `buy-${{index}}`));
const draws = [[], [], [], [], [], []];
const players = draws.map((_, index) => ({{ drawAt(time) {{ draws[index].push(time); }} }}));

wireControls();
registerReplayView("pistol", "手枪局（全员）", panels[0], players[0], "#5d86ff");
registerReplayView("buy:one", "一号", panels[1], players[1], "#ef6aa8", "一号 购买局");
if (switcher.children.length !== 2) throw new Error("initial buttons were not registered");
if (switcher.children[1].textContent !== "一号" ||
    switcher.children[1].attributes["aria-label"] !== "一号 购买局") {{
  throw new Error("Buy suffix was not limited to the accessible label");
}}
if (panels[0].hidden || !panels[1].hidden) throw new Error("pistol was not the initial view");

scrub.listeners.input({{ target: {{ value: "750" }} }});
if (timeLabel.textContent !== "15.0 / 20.0s" || playPause.textContent !== "▶") {{
  throw new Error("test playback did not move to a paused nonzero time");
}}
switcher.children[1].listeners.click();
if (!panels[0].hidden || panels[1].hidden) throw new Error("Buy view did not activate");
if (switcher.children[0].attributes["aria-pressed"] !== "false" ||
    switcher.children[1].attributes["aria-pressed"] !== "true") {{
  throw new Error("button pressed state is inconsistent");
}}
if (scrub.value !== "0" || timeLabel.textContent !== "0.0 / 20.0s" ||
    playPause.textContent !== "⏸" || draws[1][draws[1].length - 1] !== 0) {{
  throw new Error("switching players did not restart playback from zero");
}}

scrub.listeners.input({{ target: {{ value: "500" }} }});
switcher.children[1].listeners.click();
if (timeLabel.textContent !== "10.0 / 20.0s" || playPause.textContent !== "▶") {{
  throw new Error("clicking the already active player unexpectedly restarted playback");
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

const before = draws.map(values => values.length);
drawAll(7);
if (draws[0].length !== before[0] || draws[1].length !== before[1] + 1 ||
    draws.slice(2).some((values, index) => values.length !== before[index + 2])) {{
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


def test_prep_panel_renders_both_teams_and_degrades_without_data():
    script = """
const { renderPrepPanel, buildPrepRow, prepSideLabel } = require(__PREP_JS__);

function element(overrides = {}) {
  const node = {
    hidden: false, textContent: "", title: "", className: "", children: [], dataset: {},
    width: 0, height: 0, attributes: {}, style: {},
    setAttribute(name, value) { this.attributes[name] = String(value); },
    append(...nodes) { for (const item of nodes) this.children.push(item); },
    replaceChildren(...nodes) { this.children = nodes; },
    getContext() {
      return {
        clearRect() {}, beginPath() {}, arc() {}, stroke() {},
        moveTo() {}, lineTo() {}, closePath() {}, fill() {}
      };
    },
    get childElementCount() { return this.children.length; }
  };
  return Object.assign(node, overrides);
}

const elements = {
  "#prep-panel": element({ hidden: true }),
  "#prep-teams": element(),
  "#prep-compare": element({ hidden: true }),
  "#prep-insights": element({ hidden: true }),
  "#prep-map": element(),
  "#prep-meta": element(),
  "#prep-coverage": element(),
  "#empty-state": element({ hidden: false })
};
global.document = {
  querySelector(selector) { return elements[selector] || null; },
  querySelectorAll() { return []; },
  createElement() { return element(); }
};

const radar = { fire_power: { score: 65 }, marksmanship: { score: 50 }, first: { score: 70 } };
renderPrepPanel({
  available: true, map: "de_dust2", fresh: true, own_side: 1,
  stats_coverage: { with_stats: 3, total: 4 },
  teams: [
    { side: 1, label: "ours",
      aggregates: { avg_score: 2101, avg_adpr: 83, avg_season_rating: 1.09,
        avg_perfect_power: 3997, map_win_rate: 3 / 7, with_stats: 2, map_sample_low: 0 },
      players: [
        { nickname: "me", has_stats: true, score: 2101, adpr: 83, season_rating: 1.09,
          map_win_rate: 3 / 7, map_total: 7, map_sample_low: false,
          recent_results: ["win", "lose", "win"], radar, party: 1, party_size: 2 },
        { nickname: "mate", has_stats: true, score: 2101, adpr: 83, season_rating: 1.09,
          map_win_rate: 3 / 7, map_total: 7, map_sample_low: false,
          recent_results: ["lose"], radar, party: 1, party_size: 2 }
      ] },
    { side: 2, label: "theirs",
      aggregates: { avg_score: 1900, avg_adpr: 70, avg_season_rating: 0.95,
        avg_perfect_power: 3000, map_win_rate: 1 / 3, with_stats: 1, map_sample_low: 1 },
      players: [
        { nickname: "enemy", has_stats: false, score: null, adpr: null,
          season_rating: null, perfect_power: null, map_win_rate: null,
          map_total: null, map_sample_low: false, recent_results: [], radar: {},
          party: 2, party_size: 2 },
        { nickname: "enemy2", has_stats: true, score: 1900, adpr: 70, season_rating: 0.95,
          perfect_power: 3000, map_win_rate: 1 / 3, map_total: 2, map_sample_low: true,
          recent_results: ["lose"], radar, party: 2, party_size: 2 }
      ] }
  ]
});

if (elements["#prep-panel"].hidden) throw new Error("panel stayed hidden");
if (elements["#prep-map"].textContent !== "de_dust2") throw new Error("map label missing");
if (elements["#prep-meta"].textContent !== "日志实时") throw new Error("freshness missing");
if (elements["#prep-coverage"].textContent !== "平台数据 3/4 人") throw new Error("coverage missing");
if (elements["#empty-state"].hidden !== true) throw new Error("empty state still visible");

const teams = elements["#prep-teams"].children;
if (teams.length !== 2) throw new Error(`expected 2 teams, got ${teams.length}`);
if (teams[0].dataset.label !== "ours" || teams[1].dataset.label !== "theirs") {
  throw new Error("team labels lost");
}

const oursRow = teams[0].children[1].children[0];
if (oursRow.dataset.hasStats !== "true") throw new Error("stats flag missing");
const facts = oursRow.children[1].children[1].textContent;
if (!facts.includes("2101") || !facts.includes("ADR 83")) throw new Error(`facts wrong: ${facts}`);
const rates = oursRow.children[2].children;
if (!rates[0].textContent.includes("43%")) throw new Error(`map rate wrong: ${rates[0].textContent}`);
if (rates[1].textContent !== "近 5 场 胜负胜") throw new Error(`form wrong: ${rates[1].textContent}`);

const mateBadge = teams[0].children[1].children[1].children[1].children[0].children[1];
if (mateBadge.textContent !== "2 人") throw new Error(`party size missing: ${mateBadge.textContent}`);
if (!mateBadge.className.includes("prep-party-1")) throw new Error("party colour missing");

const solo = buildPrepRow({ nickname: "solo", has_stats: false, party: null, party_size: 1 });
const soloBadge = solo.children[1].children[0].children[1];
if (!soloBadge.hidden) throw new Error("solo player must not get a party badge");

if (prepSideLabel({ side: 2, label: "unknown" }) !== "B 队") {
  throw new Error("unknown side must fall back to a neutral label");
}
if (prepSideLabel({ side: 1, label: "theirs" }) !== "对方") {
  throw new Error("labelled side must use the friendly word");
}

const enemyRow = teams[1].children[1].children[0];
if (enemyRow.dataset.hasStats !== "false") throw new Error("missing stats flag on enemy");

const compare = elements["#prep-compare"];
if (compare.hidden) throw new Error("compare strip stayed hidden");
if (compare.children.length !== 5) throw new Error(`expected 5 compare rows, got ${compare.children.length}`);
const scoreRow = compare.children[0];
if (scoreRow.children[0].textContent !== "均分") throw new Error("compare label missing");
const track = scoreRow.children[1];
if (track.children[0].style.width !== "52.5%") throw new Error(`ours bar wrong: ${track.children[0].style.width}`);
if (track.children[1].style.width !== "47.5%") throw new Error(`theirs bar wrong: ${track.children[1].style.width}`);
if (scoreRow.children[2].textContent !== "2101 : 1900") {
  throw new Error(`compare values wrong: ${scoreRow.children[2].textContent}`);
}

const insights = elements["#prep-insights"];
if (insights.hidden) throw new Error("insights stayed hidden");
if (insights.children.length !== 4) throw new Error(`expected 4 insights, got ${insights.children.length}`);
const insightText = insights.children.map((item) => item.textContent).join("\\n");
if (!insightText.includes("对方开黑：enemy、enemy2（2 人）")) throw new Error(`party insight wrong: ${insightText}`);
if (!insightText.includes("均分差 201")) throw new Error(`score insight wrong: ${insightText}`);
if (!insightText.includes("对方最高分：enemy2（1900）")) throw new Error(`top player insight wrong: ${insightText}`);
if (!insightText.includes("对方 1 人无平台数据")) throw new Error(`coverage insight wrong: ${insightText}`);

// Without a session the panel must not claim "对方" (D-2: never guess).
renderPrepPanel({
  available: true, map: "de_mirage", fresh: false,
  stats_coverage: { with_stats: 1, total: 2 },
  teams: [
    { side: 1, label: "unknown",
      aggregates: { avg_score: 2000, avg_adpr: null, avg_season_rating: null,
        avg_perfect_power: null, map_win_rate: null, with_stats: 1, map_sample_low: 0 },
      players: [{ nickname: "a", has_stats: true, score: 2000, party: null,
        party_size: 1, recent_results: [], radar: {} }] },
    { side: 2, label: "unknown",
      aggregates: { avg_score: 1900, avg_adpr: null, avg_season_rating: null,
        avg_perfect_power: null, map_win_rate: null, with_stats: 1, map_sample_low: 0 },
      players: [{ nickname: "b", has_stats: true, score: 1900, party: null,
        party_size: 1, recent_results: [], radar: {} }] }
  ]
});
const neutral = elements["#prep-insights"].children
  .map((item) => item.textContent).join("\\n");
if (!neutral.includes("B 队 1900")) throw new Error(`neutral label missing: ${neutral}`);
if (neutral.includes("对方")) throw new Error(`guessed a side: ${neutral}`);

renderPrepPanel(null);
if (!elements["#prep-panel"].hidden) throw new Error("panel did not hide without a match");
if (!compare.hidden) throw new Error("compare did not hide without a match");
if (!insights.hidden) throw new Error("insights did not hide without a match");
"""
    script = script.replace("__PREP_JS__", json.dumps(os.path.abspath(PREP_JS)))
    result = subprocess.run(
        [NODE, "-e", script],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
