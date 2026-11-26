import bpy, math, sys

# args after "--": image, output file, width, height, fps, seconds
image_path, out_path = sys.argv[sys.argv.index("--") + 1], sys.argv[sys.argv.index("--") + 2]
W, H, FPS = (int(v) for v in sys.argv[sys.argv.index("--") + 3:sys.argv.index("--") + 6])
SECONDS = float(sys.argv[sys.argv.index("--") + 6])
FRAMES = max(2, int(round(SECONDS * FPS)))

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.render.engine = "BLENDER_EEVEE"
scene.render.resolution_x, scene.render.resolution_y = W, H
scene.render.resolution_percentage = 100
scene.render.fps = FPS
scene.frame_start, scene.frame_end = 1, FRAMES
scene.view_settings.view_transform = "Standard"
scene.eevee.taa_render_samples = 16

# --- picture on a subdivided plane, displaced by its own brightness (pseudo-depth) ---
img = bpy.data.images.load(image_path)
bpy.ops.mesh.primitive_plane_add(size=2.45, location=(0, 0, 0), rotation=(math.radians(90), 0, 0))
plane = bpy.context.active_object
sub = plane.modifiers.new("sub", "SUBSURF")
sub.subdivision_type = "SIMPLE"
sub.levels = sub.render_levels = 7
tex = bpy.data.textures.new("depth", "IMAGE")
tex.image = img
disp = plane.modifiers.new("disp", "DISPLACE")
disp.texture = tex
disp.texture_coords = "UV"
disp.direction = "Z"          # local +Z of the rotated plane points at the camera: brighter = nearer
disp.strength = 0.12
disp.mid_level = 0.35

mat = bpy.data.materials.new("picture")
mat.use_nodes = True
nt = mat.node_tree
nt.nodes.clear()
tnode = nt.nodes.new("ShaderNodeTexImage")
tnode.image = img
emit = nt.nodes.new("ShaderNodeEmission")
outn = nt.nodes.new("ShaderNodeOutputMaterial")
nt.links.new(tnode.outputs["Color"], emit.inputs["Color"])
nt.links.new(emit.outputs["Emission"], outn.inputs["Surface"])
plane.data.materials.append(mat)

# --- camera ---
cam_data = bpy.data.cameras.new("cam")
cam_data.lens = 50
cam = bpy.data.objects.new("cam", cam_data)
scene.collection.objects.link(cam)
scene.camera = cam
cam.rotation_euler = (math.radians(90), 0, 0)

# ===== CAMERA MOVE (edit only this section) =====
# The plane is 2.45 wide and the camera at y=-2.78 frames the middle 82% of it, so keep the
# move small enough that the edge of the plane never enters the frame:
# |x| <= 0.1, |z| <= 0.1, y between -2.9 and -2.3, tilt and roll within 1.5 degrees.
# (The safety clamp below enforces these limits whatever this section does.)
START = {"x": 0.0, "y": -2.78, "z": 0.0}
END = {"x": 0.0, "y": -2.35, "z": 0.0}      # slow push in

# Keyframe at the start
cam.location = (START["x"], START["y"], START["z"])
cam.rotation_euler = (math.radians(90), 0, 0)
cam.keyframe_insert("location", frame=1)
cam.keyframe_insert("rotation_euler", frame=1)

# Keyframe after typing slowly
typing_frames = int(FRAMES * 0.6)
cam.location = (0.0, -2.78, 0.0)
cam.rotation_euler = (math.radians(90), 0, 0)
cam.keyframe_insert("location", frame=typing_frames)
cam.keyframe_insert("rotation_euler", frame=typing_frames)

# Keyframe after leaning back
lean_back_frames = int(FRAMES * 0.7)
cam.location = (0.05, -2.75, 0.05)
cam.rotation_euler = (math.radians(89.5), math.radians(0.5), math.radians(-0.5))
cam.keyframe_insert("location", frame=lean_back_frames)
cam.keyframe_insert("rotation_euler", frame=lean_back_frames)

# Keyframe at the end with the push in
cam.location = (END["x"], END["y"], END["z"])
cam.rotation_euler = (math.radians(90), 0, 0)
cam.keyframe_insert("location", frame=FRAMES)
cam.keyframe_insert("rotation_euler", frame=FRAMES)
# =================================================

# ===== SAFETY CLAMP (do not edit): keeps the edge of the picture out of the frame =====
lim_loc = cam.constraints.new("LIMIT_LOCATION")
lim_loc.owner_space = "WORLD"
lim_loc.use_min_x = lim_loc.use_max_x = lim_loc.use_min_y = lim_loc.use_max_y = True
lim_loc.use_min_z = lim_loc.use_max_z = True
lim_loc.min_x, lim_loc.max_x = -0.10, 0.10
lim_loc.min_y, lim_loc.max_y = -2.90, -2.30
lim_loc.min_z, lim_loc.max_z = -0.10, 0.10
lim_rot = cam.constraints.new("LIMIT_ROTATION")
lim_rot.owner_space = "WORLD"
lim_rot.use_limit_x = lim_rot.use_limit_y = lim_rot.use_limit_z = True
lim_rot.min_x, lim_rot.max_x = math.radians(88.5), math.radians(91.5)
lim_rot.min_y, lim_rot.max_y = math.radians(-1.5), math.radians(1.5)
lim_rot.min_z, lim_rot.max_z = math.radians(-1.5), math.radians(1.5)
# =================================================

scene.render.filepath = out_path
scene.render.use_file_extension = False
scene.render.image_settings.media_type = "VIDEO"   # Blender 5: choose video before FFMPEG
scene.render.image_settings.file_format = "FFMPEG"
scene.render.ffmpeg.format = "MPEG4"
scene.render.ffmpeg.codec = "H264"
scene.render.ffmpeg.constant_rate_factor = "HIGH"
bpy.ops.render.render(animation=True)
