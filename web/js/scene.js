// The 3D picture: an SO-101-like arm behind a 25-key MIDI keyboard, with a Reachy Mini watching.
// Units are metres. The arm is a simplified stand-in, not the real geometry: a turning base, two
// links and a finger that always points straight down, placed by simple inverse kinematics.

import * as THREE from "three";
import { OrbitControls } from "../vendor/OrbitControls.js";

import { keyByNumber } from "./songs.js";

const WHITE_W = 0.0235; // width of a white key
const KEY_TOP = 0.045; // height of the white keys above the desk
const FIRST_MIDI = 48; // C3, the lowest of the 25 keys
const LAST_MIDI = 72; // C5
const CENTRE_WHITE = 10; // the six sticker keys (C4 to A4) are centred on x = 0
const IS_BLACK = [false, true, false, true, false, false, true, false, true, false, true, false];

const BASE = new THREE.Vector3(0, 0, -0.19); // where the arm stands
const SHOULDER_H = 0.105;
const UPPER = 0.12;
const FORE = 0.14;
const FINGER = 0.085;
const PRESS_Z = 0.015; // where along the key the finger lands
const HOVER = 0.03;
const DIP = 0.007; // how far a key goes down

const COLORS = {
  white: 0xf5f3ee,
  black: 0x1b1d22,
  down: 0x4fd1a5,
  target: 0xffdf8a,
  body: 0x2a2d35,
  link: 0xf0efe9,
  servo: 0x22252c,
  accent: 0xff8a3d,
  desk: 0x14161b,
};

const easeInOut = (t) => (t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2);
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function box(w, h, d, color, options = {}) {
  const mesh = new THREE.Mesh(
    new THREE.BoxGeometry(w, h, d),
    new THREE.MeshStandardMaterial({ color, roughness: 0.6, metalness: 0.05, ...options }),
  );
  mesh.castShadow = mesh.receiveShadow = true;
  return mesh;
}

function cylinder(radius, height, color, options = {}) {
  const mesh = new THREE.Mesh(
    new THREE.CylinderGeometry(radius, radius, height, 32),
    new THREE.MeshStandardMaterial({ color, roughness: 0.6, metalness: 0.05, ...options }),
  );
  mesh.castShadow = mesh.receiveShadow = true;
  return mesh;
}

/** A small round sticker with a number and a word, lying flat on a key. */
function sticker(number, word) {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 128;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#ffd166";
  ctx.beginPath();
  ctx.arc(64, 64, 60, 0, 2 * Math.PI);
  ctx.fill();
  ctx.fillStyle = "#1b1d22";
  ctx.textAlign = "center";
  ctx.font = "bold 62px system-ui, sans-serif";
  ctx.fillText(String(number), 64, 70);
  ctx.font = "bold 30px system-ui, sans-serif";
  ctx.fillText(word, 64, 104);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  const mesh = new THREE.Mesh(
    new THREE.CircleGeometry(0.0095, 32),
    new THREE.MeshBasicMaterial({ map: texture, transparent: true }),
  );
  mesh.rotation.x = -Math.PI / 2;
  return mesh;
}

export class Stage {
  constructor(canvas) {
    this.canvas = canvas;
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;

    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(34, 1, 0.01, 10);
    this.camera.position.set(-0.22, 0.38, 0.63);
    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.target.set(0.02, 0.05, -0.05);
    this.controls.enableDamping = true;
    this.controls.enablePan = false;
    this.controls.minDistance = 0.35;
    this.controls.maxDistance = 1.2;
    this.controls.maxPolarAngle = Math.PI / 2 - 0.05;

    this.keys = new Map(); // midi -> { mesh, restY, baseColor, downs, target }
    this.tweens = [];
    this.tip = new THREE.Vector3(0, KEY_TOP + 0.09, -0.02); // where the fingertip is
    this.chain = Promise.resolve();
    this.queued = 0;
    this.target = null;

    this.addLights();
    this.addDesk();
    this.addKeyboard();
    this.addArm();
    this.addReachy();
    this.solveArm();

    new ResizeObserver(() => this.resize()).observe(canvas.parentElement);
    this.resize();
    this.clock = new THREE.Clock();
    this.renderer.setAnimationLoop(() => this.frame());
  }

  // ------------------------------------------------------------------------------------ building

  addLights() {
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x30343c, 1.1));
    const sun = new THREE.DirectionalLight(0xffffff, 2.2);
    sun.position.set(0.35, 0.7, 0.4);
    sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048);
    Object.assign(sun.shadow.camera, { left: -0.6, right: 0.6, top: 0.6, bottom: -0.6, near: 0.1, far: 2 });
    sun.shadow.bias = -0.0005;
    this.scene.add(sun);
  }

  addDesk() {
    const desk = new THREE.Mesh(
      new THREE.CircleGeometry(0.62, 64),
      new THREE.MeshStandardMaterial({ color: COLORS.desk, roughness: 0.9 }),
    );
    desk.rotation.x = -Math.PI / 2;
    desk.receiveShadow = true;
    this.scene.add(desk);
  }

  whiteX(index) {
    return (index + 0.5 - CENTRE_WHITE) * WHITE_W;
  }

  addKeyboard() {
    const left = this.whiteX(0) - WHITE_W / 2 - 0.012;
    const right = this.whiteX(14) + WHITE_W / 2 + 0.012;
    const width = right - left;
    const centre = (left + right) / 2;

    const tray = box(width, 0.03, 0.175, COLORS.body);
    tray.position.set(centre, 0.015, -0.0275);
    this.scene.add(tray);
    const strip = box(width, 0.02, 0.058, 0x343842);
    strip.position.set(centre, 0.04, -0.084);
    this.scene.add(strip);
    for (let i = 0; i < 8; i++) {
      const knob = cylinder(0.0075, 0.012, 0xd8d5cc);
      knob.position.set(centre - 0.02 + i * 0.022, 0.056, -0.092);
      this.scene.add(knob);
    }
    for (let i = 0; i < 4; i++) {
      const pad = box(0.022, 0.004, 0.022, 0x596070, { emissive: 0x1d2a3a });
      pad.position.set(left + 0.03 + i * 0.027, 0.052, -0.086);
      this.scene.add(pad);
    }

    let white = 0;
    for (let midi = FIRST_MIDI; midi <= LAST_MIDI; midi++) {
      const black = IS_BLACK[midi % 12];
      const mesh = black
        ? box(0.013, 0.012, 0.06, COLORS.black, { roughness: 0.35 })
        : box(WHITE_W - 0.0016, 0.015, 0.1, COLORS.white, { roughness: 0.35 });
      const restY = black ? KEY_TOP + 0.004 : KEY_TOP - 0.0075;
      mesh.position.set(black ? this.whiteX(white) - WHITE_W / 2 : this.whiteX(white), restY, black ? -0.02 : 0);
      this.scene.add(mesh);
      this.keys.set(midi, { mesh, restY, baseColor: black ? COLORS.black : COLORS.white, downs: 0 });
      if (!black) white++;
    }

    for (let number = 1; number <= 6; number++) {
      const { word, midi } = keyByNumber(number);
      const label = sticker(number, word);
      label.position.set(0, 0.0078, 0.036);
      this.keys.get(midi).mesh.add(label);
    }
  }

  addArm() {
    const base = cylinder(0.042, 0.03, COLORS.servo);
    base.position.copy(BASE).setY(0.015);
    this.scene.add(base);

    this.yaw = new THREE.Group(); // turns left and right
    this.yaw.position.copy(BASE);
    this.scene.add(this.yaw);
    const column = box(0.05, SHOULDER_H - 0.03, 0.045, COLORS.link);
    column.position.y = 0.03 + (SHOULDER_H - 0.03) / 2;
    this.yaw.add(column);

    const joint = () => {
      const servo = cylinder(0.02, 0.058, COLORS.servo);
      servo.rotation.z = Math.PI / 2;
      return servo;
    };
    const link = (length) => {
      const mesh = box(0.036, 0.03, length, COLORS.link);
      mesh.position.z = length / 2;
      return mesh;
    };

    this.shoulder = new THREE.Group(); // lifts the upper arm
    this.shoulder.position.y = SHOULDER_H;
    this.shoulder.add(joint(), link(UPPER));
    this.yaw.add(this.shoulder);

    this.elbow = new THREE.Group();
    this.elbow.position.z = UPPER;
    this.elbow.add(joint(), link(FORE));
    this.shoulder.add(this.elbow);

    this.wrist = new THREE.Group(); // keeps the finger pointing down
    this.wrist.position.z = FORE;
    const hand = box(0.04, 0.034, 0.045, COLORS.servo);
    hand.position.z = 0.02;
    const finger = box(0.014, 0.014, FINGER - 0.04, COLORS.accent);
    finger.position.z = 0.04 + (FINGER - 0.04) / 2;
    this.wrist.add(joint(), hand, finger);
    this.elbow.add(this.wrist);
  }

  addReachy() {
    this.reachy = new THREE.Group();
    this.reachy.position.set(0.25, 0, 0.04);
    this.reachy.lookAt(0, 0, -0.02);
    const body = cylinder(0.045, 0.085, 0xf4f4f1);
    body.position.y = 0.0425;
    this.reachy.add(body);

    this.head = new THREE.Group();
    this.head.position.y = 0.135;
    const skull = new THREE.Mesh(
      new THREE.SphereGeometry(0.055, 48, 32),
      new THREE.MeshStandardMaterial({ color: 0xf4f4f1, roughness: 0.5 }),
    );
    skull.scale.set(1.15, 0.8, 0.95);
    skull.castShadow = true;
    this.head.add(skull);
    for (const side of [-1, 1]) {
      const eye = cylinder(0.014, 0.012, 0x15171b, { roughness: 0.2 });
      eye.rotation.x = Math.PI / 2;
      eye.position.set(side * 0.026, 0.004, 0.05);
      this.head.add(eye);
    }
    this.antennas = [-1, 1].map((side) => {
      const antenna = new THREE.Group();
      antenna.position.set(side * 0.038, 0.035, -0.005);
      const stem = cylinder(0.0022, 0.07, 0x15171b);
      stem.position.y = 0.035;
      const ball = new THREE.Mesh(
        new THREE.SphereGeometry(0.007, 16, 12),
        new THREE.MeshStandardMaterial({ color: COLORS.accent }),
      );
      ball.position.y = 0.07;
      antenna.add(stem, ball);
      antenna.rotation.z = -side * 0.25;
      antenna.userData.rest = antenna.rotation.z;
      this.head.add(antenna);
      return antenna;
    });
    this.head.rotation.x = 0.25; // looking down at the keys
    this.reachy.add(this.head);
    this.scene.add(this.reachy);
    this.mood = { wiggle: 0, tilt: 0 };
  }

  // ---------------------------------------------------------------------------------- arm motion

  /** Turn the joints so the fingertip is at this.tip, finger straight down. */
  solveArm() {
    const dx = this.tip.x - BASE.x;
    const dz = this.tip.z - BASE.z;
    const reach = Math.hypot(dx, dz);
    const height = this.tip.y + FINGER - SHOULDER_H; // the wrist, seen from the shoulder
    const distance = Math.min(Math.hypot(reach, height), UPPER + FORE - 1e-4);
    const cosElbow = (distance ** 2 - UPPER ** 2 - FORE ** 2) / (2 * UPPER * FORE);
    const bend = Math.acos(THREE.MathUtils.clamp(cosElbow, -1, 1));
    const lift = Math.atan2(height, reach) + Math.atan2(FORE * Math.sin(bend), UPPER + FORE * Math.cos(bend));
    this.yaw.rotation.y = Math.atan2(dx, dz);
    this.shoulder.rotation.x = -lift;
    this.elbow.rotation.x = bend;
    this.wrist.rotation.x = Math.PI / 2 + (lift - bend);
  }

  tween(ms, step) {
    return new Promise((resolve) => this.tweens.push({ start: performance.now(), ms, step, resolve }));
  }

  moveTip(to, ms) {
    const from = this.tip.clone();
    return this.tween(ms, (t) => {
      this.tip.lerpVectors(from, to, easeInOut(t));
      this.solveArm();
    });
  }

  keyPoint(number, lift = 0) {
    const mesh = this.keys.get(keyByNumber(number).midi).mesh;
    return new THREE.Vector3(mesh.position.x, KEY_TOP + lift, PRESS_Z);
  }

  async pressOnce(number, onDown, quick) {
    const midi = keyByNumber(number).midi;
    const hover = this.keyPoint(number, HOVER);
    const travel = this.tip.distanceTo(hover);
    await this.moveTip(hover, quick ? 120 : 260 + 2200 * travel);
    await this.moveTip(this.keyPoint(number, -DIP), quick ? 70 : 110);
    this.keyDown(midi);
    onDown?.();
    await wait(quick ? 90 : 180);
    this.keyUp(midi);
    await this.moveTip(hover, quick ? 90 : 150);
  }

  /** The arm presses a sticker key (1 to 6). Presses queue up. `onDown` runs as the key lands. */
  pressKey(number, onDown) {
    this.queued++;
    this.chain = this.chain.then(() => this.pressOnce(number, onDown, false)).finally(() => this.queued--);
    return this.chain;
  }

  /** The arm copies a press that happened on the real keyboard, if it is not already busy. */
  mirrorPress(number) {
    if (this.queued) return;
    this.queued++;
    this.chain = this.chain.then(() => this.pressOnce(number, null, true)).finally(() => this.queued--);
  }

  get busy() {
    return this.queued > 0;
  }

  // ------------------------------------------------------------------------------------- keys

  paint(entry) {
    const color = entry.downs > 0 ? COLORS.down : entry.target ? COLORS.target : entry.baseColor;
    entry.mesh.material.color.setHex(color);
    entry.mesh.material.emissive.setHex(entry.downs > 0 ? 0x0f5a45 : 0x000000);
    entry.mesh.position.y = entry.restY - (entry.downs > 0 ? DIP : 0);
  }

  keyDown(midi) {
    const entry = this.keys.get(midi);
    if (!entry) return;
    entry.downs++;
    this.paint(entry);
  }

  keyUp(midi) {
    const entry = this.keys.get(midi);
    if (!entry) return;
    entry.downs = Math.max(0, entry.downs - 1);
    this.paint(entry);
  }

  /** Light a key for a moment: a note that came from the real keyboard. */
  flash(midi, ms = 260) {
    if (!this.keys.has(midi)) return;
    this.keyDown(midi);
    setTimeout(() => this.keyUp(midi), ms);
  }

  /** Mark the sticker key (1 to 6) the lesson is about, or pass null to clear it. */
  setTarget(number) {
    for (const entry of this.keys.values()) {
      if (entry.target) {
        entry.target = false;
        this.paint(entry);
      }
    }
    this.target = number;
    if (number == null) return;
    const entry = this.keys.get(keyByNumber(number).midi);
    entry.target = true;
    this.paint(entry);
  }

  // ----------------------------------------------------------------------------------- Reachy

  celebrate() {
    this.mood.wiggle = 1.4; // seconds of happy antennas
  }

  /** Reachy leans in while it listens. */
  listening(on) {
    this.mood.tilt = on ? 1 : 0;
  }

  // ------------------------------------------------------------------------------------ frames

  resize() {
    const { clientWidth: width, clientHeight: height } = this.canvas.parentElement;
    if (!width || !height) return;
    this.renderer.setSize(width, height, false);
    this.camera.aspect = width / height;
    // On a narrow screen step back so the whole keyboard stays in view.
    const aspect = width / height;
    this.camera.fov = aspect < 0.8 ? 62 : aspect < 1.15 ? 46 : 34;
    this.camera.updateProjectionMatrix();
  }

  frame() {
    const now = performance.now();
    const dt = Math.min(this.clock.getDelta(), 0.05);
    this.tweens = this.tweens.filter((tween) => {
      const t = Math.min(1, (now - tween.start) / tween.ms);
      tween.step(t);
      if (t >= 1) tween.resolve();
      return t < 1;
    });

    this.mood.wiggle = Math.max(0, this.mood.wiggle - dt);
    const swing = this.mood.wiggle > 0 ? Math.sin(now / 45) * 0.45 * Math.min(1, this.mood.wiggle) : 0;
    this.antennas.forEach((antenna, i) => {
      antenna.rotation.z = antenna.userData.rest + (i ? swing : -swing);
    });
    const tilt = this.mood.tilt * 0.22;
    this.head.rotation.z += (tilt - this.head.rotation.z) * Math.min(1, dt * 6);

    this.controls.update();
    this.renderer.render(this.scene, this.camera);
  }
}
