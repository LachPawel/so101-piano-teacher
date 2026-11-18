# SO-101 visual assets

Original SO-101 follower CAD meshes and joint frames from
[TheRobotStudio/SO-ARM100](https://github.com/TheRobotStudio/SO-ARM100), pinned to
commit [`a758567c3978dfeefe282ede0500085a48fe8f78`](https://github.com/TheRobotStudio/SO-ARM100/tree/a758567c3978dfeefe282ede0500085a48fe8f78/Simulation/SO101).

- `so101.urdf` is an unchanged copy of `Simulation/SO101/so101_new_calib.urdf`.
- `assets/*.stl` are the 13 unchanged binary STL files referenced by that URDF.
- Upstream license: **Apache-2.0**, reproduced in [LICENSE](LICENSE).
- Copyright and attribution: TheRobotStudio and SO-ARM100 contributors.
- Upstream CAD provenance is also recorded in the URDF's opening comments.

The page loads these files locally. There are no runtime downloads, converters,
build tools, physics engines, or model weights. The scene applies an olive
material to the printed parts, based on the physical piano-teacher setup. It
closes the gripper at its URDF lower limit, adds a small silicone pad to the fixed
jaw, and drives the original joint frames
with a deterministic visual inverse-kinematics solver. This is a scripted
illustration, not a controller for physical hardware.

`../STLLoader.js`, `../RoundedBoxGeometry.js`, and `../RoomEnvironment.js` are
unmodified three.js **r170** examples, matching the existing vendored renderer.
They share the MIT license in [../THREE-LICENSE](../THREE-LICENSE).

Reachy Mini is an original procedural illustration in `../../js/scene.js`,
based on its visible silhouette (ivory shells, asymmetric camera eyes,
neck linkages, and spring antennas). No Pollen Robotics CAD meshes or images
are redistributed. The keyboard, stickers, and labels are also generated
locally by the scene.
