// A small, offline rehearsal stage. Metres throughout; the SO-101's original
// URDF frames and STL parts are retained (see vendor/so101/README.md).
import * as THREE from "three";
import { OrbitControls } from "../vendor/OrbitControls.js";
import { STLLoader } from "../vendor/STLLoader.js";
import { RoundedBoxGeometry } from "../vendor/RoundedBoxGeometry.js";
import { RoomEnvironment } from "../vendor/RoomEnvironment.js";
import { keyByNumber } from "./songs.js";

const WHITE_W = 0.0235;
const KEY_TOP = 0.045;
const KEY_BACK = -0.052;
const PRESS_Z = 0.025;
const DIP = 0.006;
const HOVER = 0.035;
const BLACK = [false, true, false, true, false, false, true, false, true, false, true, false];
const ARM_JOINTS = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll"];
// The silicone contact pad extends just beyond the fixed jaw's original mesh.
const TIP_LOCAL = new THREE.Vector3(-0.0079, -0.000218, -0.112);
const COLORS = { ivory: 0xfffbec, black: 0x242723, arm: 0x78883c, green: 0xb5d99c };
const smooth = (t) => t * t * (3 - 2 * t);
const clamp = THREE.MathUtils.clamp;
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const material = (color, options = {}) => new THREE.MeshStandardMaterial({ color, roughness: 0.42, ...options });

function mesh(geometry, finish) {
  const object = new THREE.Mesh(geometry, finish);
  object.castShadow = object.receiveShadow = true;
  return object;
}
function rounded(w, h, d, finish, radius = 0.003, segments = 3) {
  return mesh(new RoundedBoxGeometry(w, h, d, segments, radius), finish);
}
function cylinder(radius, height, finish, top = radius) {
  return mesh(new THREE.CylinderGeometry(top, radius, height, 40), finish);
}
function place(parent, object, x, y, z) {
  object.position.set(x, y, z);
  parent.add(object);
  return object;
}
function tube(points, radius, finish) {
  return mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(points.map((p) => new THREE.Vector3(...p))), 32, radius, 8, false), finish);
}
function texture(draw, width = 512, height = 256) {
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  draw(canvas.getContext("2d"), width, height);
  const map = new THREE.CanvasTexture(canvas);
  map.colorSpace = THREE.SRGBColorSpace;
  map.anisotropy = 4;
  return map;
}
function decal(map, width, depth) {
  const object = new THREE.Mesh(new THREE.PlaneGeometry(width, depth), new THREE.MeshBasicMaterial({ map, transparent: true, depthWrite: false, polygonOffset: true, polygonOffsetFactor: -2 }));
  object.rotation.x = -Math.PI / 2;
  return object;
}
function label(text, width, depth, color = "#b8b9b1") {
  return decal(texture((ctx, w, h) => {
    ctx.fillStyle = color;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.font = "600 44px monospace";
    ctx.fillText(text, w / 2, h / 2);
  }, 1024, 128), width, depth);
}
function origin(element) {
  const numbers = (name) => (element?.getAttribute(name) || "0 0 0").split(/\s+/).map(Number);
  return new THREE.Matrix4().makeRotationFromEuler(new THREE.Euler(...numbers("rpy"), "ZYX")).setPosition(...numbers("xyz"));
}

// Small damped least-squares system for the four positioning joints. The roll
// stays fixed so the narrow jaw approaches each white key in the same attitude.
function linearSolve(a, b) {
  const rows = a.map((row, i) => [...row, b[i]]);
  const n = b.length;
  for (let c = 0; c < n; c++) {
    let pivot = c;
    for (let r = c + 1; r < n; r++) if (Math.abs(rows[r][c]) > Math.abs(rows[pivot][c])) pivot = r;
    [rows[c], rows[pivot]] = [rows[pivot], rows[c]];
    const divisor = rows[c][c];
    for (let k = c; k <= n; k++) rows[c][k] /= divisor;
    for (let r = 0; r < n; r++) {
      if (r === c) continue;
      const factor = rows[r][c];
      for (let k = c; k <= n; k++) rows[r][k] -= factor * rows[c][k];
    }
  }
  return rows.map((row) => row[n]);
}

export class Stage {
  constructor(canvas) {
    this.canvas = canvas;
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.18;
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0xfaf7ee);
    this.scene.fog = new THREE.Fog(0xfaf7ee, 1.3, 3.5);
    this.camera = new THREE.PerspectiveCamera(34, 1, 0.01, 8);
    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.controls.enablePan = false;
    this.controls.minDistance = 0.42;
    this.controls.maxDistance = 2.8;
    this.controls.minPolarAngle = 0.30;
    this.controls.maxPolarAngle = Math.PI / 2 - 0.08;
    this.controls.target.set(-0.025, 0.105, 0.035);
    this.keys = new Map();
    this.tweens = [];
    this.tip = new THREE.Vector3(-0.012, KEY_TOP + HOVER, PRESS_Z);
    this.chain = Promise.resolve();
    this.queued = 0;
    this.target = null;
    this.mood = { wiggle: 0, tilt: 0 };
    this.reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    this.addCaption();
    this.addLights();
    this.addDesk();
    this.addKeyboard();
    this.addReachy();
    this.ready = this.addArm().then(() => {
      this.solveArm(60);
      this.status.textContent = "Ready to play";
      this.status.dataset.ready = "true";
    }).catch((error) => {
      this.assetError = error;
      this.status.textContent = "Robot could not load. Refresh to retry.";
      this.status.dataset.ready = "error";
    });
    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(canvas.parentElement);
    this.resize();
    this.clock = new THREE.Clock();
    this.renderer.setAnimationLoop(() => this.frame());
  }

  addCaption() {
    const caption = document.createElement("div");
    caption.className = "stage-caption";
    caption.innerHTML = '<div><span class="stage-eyebrow">A LITTLE HUMAN–ROBOT HARMONY</span><p>Make a little<br>music.</p></div><span class="stage-status" role="status">Loading robot…</span>';
    this.canvas.parentElement.append(caption);
    this.status = caption.querySelector(".stage-status");
    const legend = document.createElement("div");
    legend.className = "stage-legend";
    legend.innerHTML = '<span><i></i> SO-101</span><span><i></i> Reachy Mini</span><span>25 keys / C3–C5</span>';
    this.canvas.parentElement.append(legend);
  }

  addLights() {
    const pmrem = new THREE.PMREMGenerator(this.renderer);
    const room = new RoomEnvironment();
    this.environment = pmrem.fromScene(room, 0.04);
    this.scene.environment = this.environment.texture;
    this.scene.environmentIntensity = 0.35;
    room.dispose();
    pmrem.dispose();
    this.scene.add(new THREE.HemisphereLight(0xfff8e8, 0xc8b68c, 1.7));
    const key = new THREE.DirectionalLight(0xfff3d4, 3.0);
    key.position.set(-0.35, 0.85, 0.45);
    key.castShadow = true;
    key.shadow.mapSize.set(2048, 2048);
    Object.assign(key.shadow.camera, { left: -0.6, right: 0.6, top: 0.6, bottom: -0.6, near: 0.1, far: 2 });
    key.shadow.normalBias = 0.00065;
    key.shadow.bias = -0.00012;
    key.shadow.radius = 3;
    this.scene.add(key);
    const rim = new THREE.DirectionalLight(0xffffff, 1.3);
    rim.position.set(0.4, 0.5, -0.4);
    this.scene.add(rim);
    const fill = new THREE.DirectionalLight(0xffffff, 0.35);
    fill.position.set(0.2, 0.15, 0.7);
    this.scene.add(fill);
  }

  addDesk() {
    // A seamless cream surface with only the robots' shadows visible. This
    // keeps the scene continuous with the page instead of inside a 3D box.
    const floor = mesh(new THREE.PlaneGeometry(200, 200), new THREE.ShadowMaterial({ color: 0x79714d, opacity: 0.24 }));
    floor.rotation.x = -Math.PI / 2;
    floor.position.y = 0;
    this.scene.add(floor);
    // Soft rubber mounting plate under the real CAD base.
    place(this.scene, rounded(0.12, 0.006, 0.11, material(0x111614), 0.008), 0, 0.003, 0.24);
  }

  whiteX(index) { return (index + 0.5 - 10) * WHITE_W; }

  addKeyboard() {
    const centre = this.whiteX(7);
    const width = 15 * WHITE_W + 0.035;
    const dark = material(0x1a1e20, { metalness: 0.35, roughness: 0.35 });
    const edge = material(0x343939, { metalness: 0.45, roughness: 0.3 });
    place(this.scene, rounded(width, 0.034, 0.188, dark, 0.009), centre, 0.021, -0.028);
    place(this.scene, rounded(width - 0.007, 0.017, 0.062, edge, 0.003), centre, 0.045, -0.088);
    for (const x of [centre - width / 2 + 0.005, centre + width / 2 - 0.005]) {
      place(this.scene, rounded(0.01, 0.043, 0.179, dark, 0.004), x, 0.025, -0.027);
      for (const z of [-0.107, 0.052]) place(this.scene, cylinder(0.0015, 0.0005, material(0x7d8582, { metalness: 0.8 })), x, 0.047, z);
    }
    place(this.scene, label("P I A N O  /  2 5", 0.085, 0.008), centre + 0.114, 0.054, -0.098);
    const screen = rounded(0.043, 0.001, 0.022, material(0x111a18), 0.002);
    place(this.scene, screen, centre + 0.005, 0.055, -0.087);
    const display = texture((ctx, w, h) => {
      ctx.fillStyle = "#121e1c"; ctx.fillRect(0, 0, w, h);
      ctx.fillStyle = "#a4d4c3"; ctx.font = "48px monospace"; ctx.fillText("01  PIANO", 24, 76);
      ctx.fillStyle = "#557c6f"; ctx.font = "26px monospace"; ctx.fillText("OCT 0     MIDI", 24, 122);
    }, 400, 150);
    place(this.scene, decal(display, 0.038, 0.015), centre + 0.005, 0.0556, -0.087);
    for (let i = 0; i < 4; i++) {
      const x = centre - 0.14 + i * 0.026;
      place(this.scene, rounded(0.021, 0.004, 0.02, material(i === 0 ? 0xd6974e : 0x5b625e, { roughness: 0.7 }), 0.003), x, 0.055, -0.083);
      place(this.scene, label(String(i + 1), 0.004, 0.004, "#d8d9ce"), x, 0.0573, -0.083);
    }
    for (let i = 0; i < 4; i++) {
      const x = centre + 0.049 + i * 0.026;
      place(this.scene, cylinder(0.007, 0.009, material(0x0c1112, { roughness: 0.6 }), 0.006), x, 0.058, -0.077);
      place(this.scene, rounded(0.001, 0.0005, 0.004, material(0xd4cdbb), 0.0002), x, 0.0627, -0.08);
    }
    let white = 0;
    for (let midi = 48; midi <= 72; midi++) {
      const black = BLACK[midi % 12];
      const baseColor = black ? COLORS.black : COLORS.ivory;
      const pivot = new THREE.Group();
      pivot.position.set(black ? this.whiteX(white) - WHITE_W / 2 : this.whiteX(white), black ? KEY_TOP + 0.013 : KEY_TOP, KEY_BACK);
      const key = rounded(black ? 0.0128 : WHITE_W - 0.0011, black ? 0.014 : 0.013, black ? 0.065 : 0.109, material(baseColor, { roughness: black ? 0.3 : 0.24 }), 0.0013);
      place(pivot, key, 0, black ? -0.007 : -0.0065, black ? 0.0325 : 0.0545);
      this.scene.add(pivot);
      this.keys.set(midi, { mesh: key, pivot, black, baseColor, downs: 0, armDepth: 0, midiDepth: 0, depth: 0, target: false });
      if (!black) white++;
    }
    for (let number = 1; number <= 6; number++) {
      const { word, midi } = keyByNumber(number);
      const sticker = texture((ctx, w, h) => {
        ctx.fillStyle = "#eec77c";
        ctx.beginPath(); ctx.roundRect(8, 8, w - 16, h - 16, 35); ctx.fill();
        ctx.strokeStyle = "#a57b36"; ctx.lineWidth = 2; ctx.stroke();
        ctx.fillStyle = "#322a1c"; ctx.textAlign = "center";
        ctx.font = "700 116px system-ui"; ctx.fillText(String(number), w / 2, 132);
        ctx.font = "600 47px system-ui"; ctx.fillText(word, w / 2, 206);
      }, 256, 256);
      place(this.keys.get(midi).pivot, decal(sticker, 0.017, 0.018), 0, 0.0002, 0.091);
    }
    // A USB lead, with a quiet curve off the back of the controller.
    const cable = material(0x101411, { roughness: 0.8 });
    this.scene.add(tube([[centre - 0.13, 0.024, -0.12], [centre - 0.15, 0.012, -0.17], [centre - 0.22, 0.004, -0.21], [centre - 0.26, 0.004, -0.31]], 0.002, cable));
  }

  async addArm() {
    const url = new URL("../vendor/so101/so101.urdf", import.meta.url);
    const response = await fetch(url);
    if (!response.ok) throw new Error(`SO-101: ${response.status}`);
    const xml = new DOMParser().parseFromString(await response.text(), "text/xml");
    const robot = xml.querySelector("robot");
    if (!robot) throw new Error("Invalid SO-101 URDF");
    const loader = new STLLoader();
    const geometries = new Map();
    for (const element of robot.querySelectorAll("visual mesh")) {
      const filename = element.getAttribute("filename");
      if (!geometries.has(filename)) geometries.set(filename, loader.loadAsync(new URL(filename, url).href));
    }
    await Promise.all(geometries.values());
    const finishes = {
      "3d_printed": material(COLORS.arm, { roughness: 0.72, metalness: 0 }),
      sts3215: material(0x202625, { roughness: 0.42, metalness: 0.25 }),
    };
    this.arm = new THREE.Group();
    this.arm.matrixAutoUpdate = false;
    // Convert URDF Z-up into Three Y-up, facing the keyboard.
    this.arm.matrix.makeRotationY(Math.PI / 2).multiply(new THREE.Matrix4().makeRotationX(-Math.PI / 2)).setPosition(0, 0.008, 0.25);
    const links = new Map();
    for (const link of robot.querySelectorAll(":scope > link")) {
      const group = new THREE.Group();
      group.name = link.getAttribute("name");
      for (const visual of link.querySelectorAll(":scope > visual")) {
        const filename = visual.querySelector("mesh").getAttribute("filename");
        const part = mesh(await geometries.get(filename), finishes[visual.querySelector("material").getAttribute("name")]);
        part.applyMatrix4(origin(visual.querySelector("origin")));
        group.add(part);
      }
      links.set(group.name, group);
    }
    this.joints = new Map();
    for (const joint of robot.querySelectorAll(":scope > joint")) {
      const name = joint.getAttribute("name");
      const frame = new THREE.Group();
      frame.applyMatrix4(origin(joint.querySelector("origin")));
      const rotation = new THREE.Group();
      frame.add(rotation);
      rotation.add(links.get(joint.querySelector("child").getAttribute("link")));
      links.get(joint.querySelector("parent").getAttribute("link")).add(frame);
      const limits = joint.querySelector("limit");
      this.joints.set(name, { rotation, origin: origin(joint.querySelector("origin")), limits: limits ? [+limits.getAttribute("lower"), +limits.getAttribute("upper")] : [0, 0] });
    }
    this.arm.add(links.get("base_link"));
    this.scene.add(this.arm);
    this.armChain = ARM_JOINTS.map((name) => this.joints.get(name));
    this.angles = [0, -0.2, 0.4, 1.2, Math.PI / 2];
    this.joints.get("gripper").rotation.rotation.z = this.joints.get("gripper").limits[0];
    this.contactPad = mesh(new THREE.SphereGeometry(0.004, 20, 12), material(0x323632, { roughness: 0.92 }));
    this.contactPad.position.copy(TIP_LOCAL).z += 0.004;
    links.get("gripper_link").add(this.contactPad);
    // A real scene-graph point, also useful for checking fingertip/key contact.
    this.fingertip = new THREE.Object3D();
    this.fingertip.position.copy(TIP_LOCAL);
    links.get("gripper_link").add(this.fingertip);
  }

  addReachy() {
    const shell = material(0xf0eee7, { roughness: 0.28 });
    const seam = material(0xb7bab2, { roughness: 0.42 });
    const black = material(0x121616, { roughness: 0.25, metalness: 0.18 });
    const metal = material(0xa0aaa5, { roughness: 0.25, metalness: 0.85 });
    this.reachy = new THREE.Group();
    this.reachy.position.set(0.31, 0, 0.005);
    this.reachy.rotation.y = -0.72;
    this.scene.add(this.reachy);
    place(this.reachy, cylinder(0.049, 0.011, black), 0, 0.006, 0);
    const profile = new THREE.SplineCurve([
      [0, 0.011], [0.043, 0.011], [0.051, 0.015], [0.057, 0.027], [0.059, 0.054],
      [0.057, 0.079], [0.051, 0.100], [0.040, 0.117], [0.026, 0.126], [0.021, 0.125],
      [0.026, 0.112], [0.035, 0.094], [0, 0.094],
    ].map(([x, y]) => new THREE.Vector2(x, y)));
    const body = new THREE.LatheGeometry(profile.getPoints(100), 64);
    const torso = mesh(body, shell);
    torso.scale.z = 0.85;
    this.reachy.add(torso);
    const bodySeam = mesh(new THREE.TorusGeometry(0.0585, 0.00045, 8, 80), seam);
    bodySeam.rotation.x = Math.PI / 2;
    bodySeam.scale.y = 0.85;
    place(this.reachy, bodySeam, 0, 0.044, 0);
    for (const side of [-1, 1]) {
      this.reachy.add(tube([[side * 0.027, 0.097, -0.003], [side * 0.025, 0.123, -0.002], [side * 0.016, 0.144, 0]], 0.0034, metal));
      const joint = cylinder(0.006, 0.008, black);
      joint.rotation.x = Math.PI / 2;
      place(this.reachy, joint, side * 0.016, 0.142, 0);
    }
    this.reachy.add(tube([[-0.025, 0.103, 0], [-0.031, 0.127, 0], [-0.019, 0.145, 0.008]], 0.0012, material(0xa84d36)));
    this.head = new THREE.Group();
    this.head.position.set(0, 0.17, 0.003);
    this.reachy.add(this.head);
    place(this.head, rounded(0.131, 0.084, 0.077, seam, 0.026, 7), 0, 0, -0.001);
    place(this.head, rounded(0.133, 0.085, 0.057, shell, 0.025, 7), 0, 0.001, 0.011);
    place(this.head, rounded(0.127, 0.08, 0.025, shell, 0.021, 7), 0, 0, -0.028);
    place(this.head, rounded(0.07, 0.006, 0.003, black, 0.002), 0, -0.002, 0.041);
    for (const [side, radius] of [[-1, 0.0215], [1, 0.017]]) {
      const eye = new THREE.Group();
      eye.rotation.x = Math.PI / 2;
      place(this.head, eye, side * 0.032, -0.001, 0.042);
      place(eye, cylinder(radius, 0.006, black), 0, 0, 0);
      place(eye, cylinder(radius * 0.84, 0.002, metal), 0, 0.0035, 0);
      place(eye, cylinder(radius * 0.78, 0.003, black), 0, 0.005, 0);
      const lens = mesh(new THREE.SphereGeometry(radius * 0.64, 32, 20), new THREE.MeshPhysicalMaterial({ color: 0x0b161a, roughness: 0.1, metalness: 0.4, clearcoat: 1, clearcoatRoughness: 0.08 }));
      lens.scale.y = 0.3;
      place(eye, lens, 0, 0.007, 0);
    }
    // Slender aerials with their characteristic exposed helical springs.
    this.antennas = [-1, 1].map((side) => {
      const antenna = new THREE.Group();
      place(this.head, antenna, side * 0.055, 0.029, -0.013);
      place(antenna, cylinder(0.0043, 0.013, shell), 0, 0.004, 0);
      place(antenna, cylinder(0.0011, 0.102, black), 0, 0.056, 0);
      const points = [];
      for (let i = 0; i <= 160; i++) {
        const phase = i / 160 * Math.PI * 16;
        points.push([Math.cos(phase) * 0.0025, 0.013 + i / 160 * 0.021, Math.sin(phase) * 0.0025]);
      }
      antenna.add(tube(points, 0.00065, metal));
      place(antenna, cylinder(0.0018, 0.02, black), 0, 0.105, 0);
      antenna.rotation.z = -side * 0.17;
      antenna.userData.rest = antenna.rotation.z;
      return antenna;
    });
    const badge = label("pollen", 0.023, 0.006, "#59635d");
    badge.rotation.x = 0;
    place(this.reachy, badge, 0, 0.063, 0.0503);
    this.head.rotation.set(0.30, -0.35, -0.035);
  }

  forward(angles) {
    const matrix = this.arm.matrix.clone();
    for (let i = 0; i < this.armChain.length; i++) matrix.multiply(this.armChain[i].origin).multiply(new THREE.Matrix4().makeRotationZ(angles[i]));
    const point = TIP_LOCAL.clone().applyMatrix4(matrix);
    const direction = new THREE.Vector3(0, 0, -1).transformDirection(matrix).multiplyScalar(0.10);
    return [...point.toArray(), ...direction.toArray()];
  }

  solveArm(iterations = 10) {
    if (!this.armChain) return;
    const target = [...this.tip.toArray(), 0, -0.1, 0];
    for (let iteration = 0; iteration < iterations; iteration++) {
      const current = this.forward(this.angles);
      const error = target.map((v, i) => v - current[i]);
      if (Math.hypot(...error) < 0.000005) break;
      const jacobian = this.angles.slice(0, 4).map((_, j) => {
        const perturbed = this.angles.slice();
        perturbed[j] += 0.0001;
        return this.forward(perturbed).map((v, k) => (v - current[k]) / 0.0001);
      });
      const a = jacobian.map((u, i) => jacobian.map((v, j) => u.reduce((s, x, k) => s + x * v[k], 0) + (i === j ? 0.00001 : 0)));
      const b = jacobian.map((u) => u.reduce((s, x, k) => s + x * error[k], 0));
      linearSolve(a, b).forEach((step, i) => {
        this.angles[i] = clamp(this.angles[i] + clamp(step, -0.18, 0.18), ...this.armChain[i].limits);
      });
    }
    this.armChain.forEach((joint, i) => { joint.rotation.rotation.z = this.angles[i]; });
  }

  tween(ms, step) {
    return new Promise((resolve) => this.tweens.push({ start: performance.now(), ms, step, resolve }));
  }
  moveTip(to, ms) {
    const from = this.tip.clone();
    return this.tween(ms, (t) => {
      this.tip.lerpVectors(from, to, smooth(t));
      this.solveArm();
    });
  }
  keyPoint(number, lift = 0) {
    const entry = this.keys.get(keyByNumber(number).midi);
    entry.pivot.updateWorldMatrix(true, false);
    return entry.pivot.localToWorld(new THREE.Vector3(0, 0.00025, PRESS_Z - KEY_BACK)).add(new THREE.Vector3(0, lift, 0));
  }
  async pressOnce(number, onDown, quick) {
    await this.ready;
    if (this.assetError) return;
    const entry = this.keys.get(keyByNumber(number).midi);
    const hover = this.keyPoint(number, HOVER);
    // Lift clear of the black keys before moving sideways to the next note.
    if (this.tip.y < KEY_TOP + HOVER - 0.002) await this.moveTip(this.tip.clone().setY(KEY_TOP + HOVER), 90);
    await this.moveTip(hover, quick ? 140 : 240 + 1100 * this.tip.distanceTo(hover));
    await this.moveTip(this.keyPoint(number), quick ? 70 : 130);
    this.contactKey = number;
    try {
      await this.tween(quick ? 60 : 95, (t) => { entry.armDepth = smooth(t); });
      onDown?.();
      await wait(quick ? 85 : 170);
    } finally {
      await this.tween(quick ? 70 : 110, (t) => { entry.armDepth = 1 - smooth(t); });
      this.contactKey = null;
      await this.moveTip(this.keyPoint(number, HOVER), quick ? 100 : 170);
    }
  }
  pressKey(number, onDown) {
    if (!Number.isInteger(number) || !keyByNumber(number)) return Promise.reject(new RangeError("Use a sticker number from 1 to 6"));
    this.queued++;
    const press = this.chain.catch(() => {}).then(() => this.pressOnce(number, onDown, false)).finally(() => { this.queued--; });
    this.chain = press;
    return press;
  }
  mirrorPress(number) {
    if (this.busy || !Number.isInteger(number) || !keyByNumber(number)) return;
    this.queued++;
    this.chain = this.chain.catch(() => {}).then(() => this.pressOnce(number, null, true)).finally(() => { this.queued--; });
    return this.chain;
  }
  get busy() { return this.queued > 0; }
  /** Live mode: glide over a sticker key without pressing it, where the real arm is heading. */
  aim(number) {
    if (!keyByNumber(number)) return Promise.resolve();
    this.queued++;
    this.chain = this.chain.catch(() => {}).then(async () => {
      await this.ready;
      if (this.assetError) return;
      if (this.tip.y < KEY_TOP + HOVER - 0.002) await this.moveTip(this.tip.clone().setY(KEY_TOP + HOVER), 90);
      const hover = this.keyPoint(number, HOVER);
      await this.moveTip(hover, 300 + 1300 * this.tip.distanceTo(hover));
    }).finally(() => { this.queued--; });
    return this.chain;
  }
  /** Live mode: a quick press of a sticker key the real arm just pressed, after whatever is queued. */
  follow(number) {
    if (!keyByNumber(number)) return Promise.resolve();
    this.queued++;
    this.chain = this.chain.catch(() => {}).then(() => this.pressOnce(number, null, true)).finally(() => { this.queued--; });
    return this.chain;
  }

  paint(entry) {
    const down = entry.depth > 0.04;
    entry.mesh.material.color.setHex(down ? COLORS.green : entry.target ? 0xf3ce86 : entry.baseColor);
    entry.mesh.material.emissive.setHex(down ? 0x153a29 : entry.target ? 0x2e1904 : 0x000000);
    entry.mesh.material.emissiveIntensity = down ? 0.25 : 0.12;
    entry.pivot.rotation.x = Math.asin(DIP * entry.depth / (PRESS_Z - KEY_BACK));
  }
  keyDown(midi) { const entry = this.keys.get(midi); if (entry) entry.downs++; }
  keyUp(midi) { const entry = this.keys.get(midi); if (entry) entry.downs = Math.max(0, entry.downs - 1); }
  flash(midi, ms = 260) {
    if (!this.keys.has(midi)) return;
    this.keyDown(midi);
    setTimeout(() => this.keyUp(midi), ms);
  }
  setTarget(number) {
    this.target = number;
    for (const [midi, entry] of this.keys) {
      entry.target = number != null && keyByNumber(number)?.midi === midi;
      this.paint(entry);
    }
  }
  celebrate() { this.mood.wiggle = 1.4; }
  listening(on) { this.mood.tilt = on ? 1 : 0; }

  resize() {
    const { clientWidth: width, clientHeight: height } = this.canvas.parentElement;
    if (!width || !height) return;
    this.renderer.setSize(width, height, false);
    this.camera.aspect = width / height;
    // Keep a consistent three-quarter view; fit width as well as height.
    const distance = Math.max(1.04, 1.03 / this.camera.aspect);
    const direction = new THREE.Vector3(-0.62, 0.63, 0.86).normalize();
    this.camera.position.copy(this.controls.target).addScaledVector(direction, distance);
    this.camera.updateProjectionMatrix();
    this.controls.update();
  }

  frame() {
    const now = performance.now();
    const dt = Math.min(this.clock.getDelta(), 0.05);
    this.tweens = this.tweens.filter((tween) => {
      const t = Math.min(1, (now - tween.start) / tween.ms);
      tween.step(t);
      if (t === 1) tween.resolve();
      return t < 1;
    });
    for (const entry of this.keys.values()) {
      const to = entry.downs > 0 ? 1 : 0;
      entry.midiDepth = THREE.MathUtils.lerp(entry.midiDepth, to, 1 - Math.exp(-dt * 35));
      if (Math.abs(entry.midiDepth - to) < 0.001) entry.midiDepth = to;
      const depth = Math.max(entry.armDepth, entry.midiDepth);
      if (Math.abs(depth - entry.depth) > 0.00001) { entry.depth = depth; this.paint(entry); }
    }
    if (this.contactKey != null) { this.tip.copy(this.keyPoint(this.contactKey)); this.solveArm(); }
    this.mood.wiggle = Math.max(0, this.mood.wiggle - dt);
    const swing = !this.reducedMotion && this.mood.wiggle > 0 ? Math.sin(now / 90) * 0.25 * Math.min(1, this.mood.wiggle) : 0;
    this.antennas.forEach((antenna, i) => { antenna.rotation.z = antenna.userData.rest + (i ? swing : -swing); });
    const keyX = this.target ? this.keys.get(keyByNumber(this.target).midi)?.pivot.position.x ?? 0 : 0;
    const nod = this.reducedMotion ? 0 : Math.sin(now / 1400) * 0.014;
    const damping = 1 - Math.exp(-dt * 5);
    this.head.rotation.x = THREE.MathUtils.lerp(this.head.rotation.x, 0.30 + this.mood.tilt * 0.12 + nod, damping);
    this.head.rotation.y = THREE.MathUtils.lerp(this.head.rotation.y, -0.35 + keyX * 1.2, damping);
    this.head.rotation.z = THREE.MathUtils.lerp(this.head.rotation.z, -0.035 + this.mood.tilt * 0.10 + swing * 0.1, damping);
    this.controls.update();
    this.renderer.render(this.scene, this.camera);
  }
}
