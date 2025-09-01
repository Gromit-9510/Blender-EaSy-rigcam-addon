bl_info = {
    "name": "EaSy Rig Cam",
    "author": "Insoo Chang",
    "version": (1, 2, 3),
    "blender": (4, 2, 0),
    "location": "View3D > N-Panel > Rig Cam & Properties > Rig Cam",
    "description": "Multi Crane Camera Rig Controller with Presets and Camera Switching",
    "category": "Animation",
    "support": "COMMUNITY",
    "doc_url": "https://github.com/Gromit-9510/Blender-EaSy-rigcam-addon",  
    "tracker_url": "https://github.com/Gromit-9510/Blender-EaSy-rigcam-addon/issues",  
}

import bpy
from bpy.types import Panel, Operator, PropertyGroup
from bpy.props import StringProperty, BoolProperty, FloatProperty, IntProperty
from mathutils import Vector
import json


# --- RigCam: Enhanced viewport and driver update system ---
def force_viewport_update(context):
    """강력한 뷰포트 및 드라이버 업데이트"""
    try:
        import bpy
        scene = context.scene if context and getattr(context, 'scene', None) else bpy.context.scene
        if scene is None:
            return
        
        # 1. 드라이버 강제 재평가를 위한 프레임 재설정
        cur = scene.frame_current
        scene.frame_set(cur)
        
        # 2. Depsgraph 업데이트 (드라이버/컨스트레인트/모디파이어 재계산)
        try:
            depsgraph = (context or bpy.context).evaluated_depsgraph_get()
            depsgraph.update()
        except Exception:
            pass
        
        # 3. View layer 업데이트
        try:
            (context or bpy.context).view_layer.update()
        except Exception:
            pass
        
        # 4. 모든 3D 뷰포트 리드로우
        try:
            ctx = context or bpy.context
            for window in ctx.window_manager.windows:
                for area in window.screen.areas:
                    if area.type == 'VIEW_3D':
                        area.tag_redraw()
        except Exception:
            pass
        
        # 5. Blender 4.2+ 추가 스왑
        try:
            if bpy.app.version >= (4, 2, 0):
                try:
                    bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
                except Exception:
                    pass
        except Exception:
            pass
    except Exception:
        pass


def set_custom_prop_and_update(obj, prop_name, value, context):
    """커스텀 프로퍼티 설정 후 드라이버 즉시 업데이트 (키프레임 되돌림 방지)"""
    if obj and prop_name in obj:
        # 1. 값 설정
        obj[prop_name] = value
        
        # 2. 오브젝트 데이터 업데이트 태그
        obj.update_tag(refresh={'DATA'})
        
        # 3. 드라이버만 즉시 업데이트 (scene.frame_set 사용 안함)
        try:
            depsgraph = context.evaluated_depsgraph_get()
            depsgraph.update()
        except Exception:
            pass
        
        # 4. 뷰포트만 리드로우 (키프레임 되돌림 방지)
        try:
            for window in context.window_manager.windows:
                for area in window.screen.areas:
                    if area.type == 'VIEW_3D':
                        area.tag_redraw()
        except Exception:
            pass


def auto_keyframe_after_snap(obj, changed_props, context):
    """스냅 후 자동으로 해당 프로퍼티들에 키프레임 삽입 (기존 키 덮어쓰기)"""
    if obj and changed_props:
        for prop_name in changed_props:
            if prop_name in obj:
                obj.keyframe_insert(data_path=f'["{prop_name}"]')


def get_object_center(obj):
    """Get the center of mass/bounding box center for any object type"""
    if obj.type == 'MESH':
        # Calculate bounding box center properly
        bbox_corners = []
        for corner in obj.bound_box:
            # Convert to Vector and transform to world space
            local_corner = Vector(corner)
            world_corner = obj.matrix_world @ local_corner
            bbox_corners.append(world_corner)
        
        # Calculate center from all corners
        center = Vector((0, 0, 0))
        for corner in bbox_corners:
            center += corner
        center /= len(bbox_corners)
        return center
    else:
        # For non-mesh objects, use origin location
        return obj.matrix_world.translation


class RigCamProperties(PropertyGroup):
    """Rig Cam 설정용 프로퍼티들"""
    
    active_rig: StringProperty(name="Active Rig", default="")
    
    lock_crane_pos: BoolProperty(name="Lock Crane Position", default=False)
    lock_crane_rot: BoolProperty(name="Lock Crane Rotation", default=False) 
    lock_camera: BoolProperty(name="Lock Camera", default=False)
    lock_settings: BoolProperty(name="Lock Settings", default=False)
    lock_focus: BoolProperty(name="Lock Focus", default=False)
    
    # 프레임 점프 설정
    frame_jump_step: IntProperty(name="Frame Step", default=30, min=1, max=1000)


def get_rig_cameras():
    """씬의 모든 리그 카메라들 찾기"""
    rig_cameras = []
    for obj in bpy.data.objects:
        if (obj.name == "CAMERA" or obj.name.endswith("_CAMERA")) and obj.type == 'EMPTY':
            if "1.Cam_Distance" in obj:
                rig_cameras.append(obj.name)
    return rig_cameras


class RIGCAM_OT_create_rig(Operator):
    """새 리그캠 생성"""
    bl_idname = "rigcam.create_rig"
    bl_label = "Create New Rig Cam"
    bl_description = "Create a new crane camera rig"
    
    rig_name: StringProperty(name="Rig Name", default="RigCam")
    
    def execute(self, context):
        base_name = self.rig_name
        counter = 1
        final_name = base_name
        
        # 이름 중복 체크
        if bpy.data.objects.get("CAMERA") and base_name == "RigCam":
            while bpy.data.objects.get(f"{base_name}_{counter:02d}_CAMERA"):
                counter += 1
            final_name = f"{base_name}_{counter:02d}"
        elif base_name != "RigCam":
            while bpy.data.objects.get(f"{final_name}_CAMERA"):
                final_name = f"{base_name}_{counter:02d}"
                counter += 1
        
        # 컬렉션 생성
        collection = bpy.data.collections.new(f"{final_name}_RigCam")
        context.scene.collection.children.link(collection)
        
        # 원본 구조 그대로 생성 (정확한 순서)
        # 1. CAMERA (컨트롤러 Empty) - 최상위
        bpy.ops.object.empty_add(type='PLAIN_AXES', location=(0, 0, 0))
        camera_empty = context.active_object
        camera_empty.show_name = True
        
        # 2. Crane Empty - Parent: CAMERA
        bpy.ops.object.empty_add(type='SINGLE_ARROW', location=(0, 0, 0))
        crane_empty = context.active_object
        crane_empty.parent = camera_empty
        
        # 3. Focus Empty - Parent: Crane (Crane과 같은 위치에)
        bpy.ops.object.empty_add(type='SPHERE', location=(0, 0, 0))
        focus_empty = context.active_object
        focus_empty.parent = crane_empty
        focus_empty.empty_display_size = 0.2  # 20cm
        
        # 4. Position Empty - Parent: Crane  
        bpy.ops.object.empty_add(type='CUBE', location=(0, 0, 0))
        position_empty = context.active_object
        position_empty.parent = crane_empty
        
        # 5. Rotation Empty - Parent: Position (Delta Transform 설정)
        bpy.ops.object.empty_add(type='ARROWS', location=(0, 0, 0))
        rotation_empty = context.active_object
        rotation_empty.parent = position_empty
        # Delta Transform: X축 90도, Z축 90도 회전
        rotation_empty.delta_rotation_euler[0] = 1.5708  # 90도 (라디안)
        rotation_empty.delta_rotation_euler[2] = 1.5708  # 90도 (라디안)
        
        # 6. Camera (실제 카메라) - Parent: Rotation
        bpy.ops.object.camera_add(location=(0, 0, 0))
        camera_obj = context.active_object
        camera_obj.parent = rotation_empty
        
        # Set initial camera rotation to 0,0,0
        camera_obj.rotation_euler = (0, 0, 0)
        
        # 이름 설정 및 Display Size 조정
        if final_name == "RigCam":
            camera_obj.name = "Camera"
            camera_empty.name = "CAMERA"
            camera_empty.empty_display_size = 0.0001  # 0.01cm
            crane_empty.name = "Crane" 
            crane_empty.empty_display_size = 0.15  # 15cm
            focus_empty.name = "Focus"
            position_empty.name = "Position"
            rotation_empty.name = "Rotation"
        else:
            camera_obj.name = f"{final_name}_Camera"
            camera_empty.name = f"{final_name}_CAMERA"
            camera_empty.empty_display_size = 0.0001  # 0.01cm
            crane_empty.name = f"{final_name}_Crane"
            crane_empty.empty_display_size = 0.15  # 15cm
            focus_empty.name = f"{final_name}_Focus"
            position_empty.name = f"{final_name}_Position"
            rotation_empty.name = f"{final_name}_Rotation"
        
        # 컨스트레인트 추가
        limit_rot = crane_empty.constraints.new('LIMIT_ROTATION')
        
        # 커스텀 프로퍼티들 추가 (초기값 - 새 리그는 모두 0으로 시작)
        if final_name == "RigCam":
            # 첫 번째 리그는 원본 값 사용
            props_to_add = {
                "1.Cam_Distance": 3.130000114440918,
                "2.Crane_ROT_H": -0.414690226316452, 
                "3.Crane_ROT_P": 0.5536184310913086,
                "4.CAM_ROT_H": 0.0, 
                "5.CAM_ROT_P": 0.0, 
                "6.CAM_ROT_B": 0.0,
                "7.CAM_POS_Z": 0.0, 
                "8.CAM_POS_Y": 0.0,
                "9.Focal_lenght": 80.0, 
                "Z.DOF": True,
                "0.Crane_POS_X": 0.0, 
                "0.Crane_POS_Y": 0.0, 
                "0.Crane_POS_Z": 0.0,
                "92.FocusDist": 3.138000011444092,
                "9.ClipStartDist": 0.0009999999310821295,
                "9.F_stop": 6.0
            }
        else:
            # 두 번째 이후 리그는 깔끔한 초기값
            props_to_add = {
                "1.Cam_Distance": 5.0,
                "2.Crane_ROT_H": 0.0, 
                "3.Crane_ROT_P": 0.0,
                "4.CAM_ROT_H": 0.0, 
                "5.CAM_ROT_P": 0.0, 
                "6.CAM_ROT_B": 0.0,
                "7.CAM_POS_Z": 0.0, 
                "8.CAM_POS_Y": 0.0,
                "9.Focal_lenght": 50.0, 
                "Z.DOF": False,
                "0.Crane_POS_X": 0.0, 
                "0.Crane_POS_Y": 0.0, 
                "0.Crane_POS_Z": 0.0,
                "92.FocusDist": 5.0,
                "9.ClipStartDist": 0.001,
                "9.F_stop": 2.8
            }
        
        for prop, value in props_to_add.items():
            camera_empty[prop] = value
        
        # 모든 오브젝트를 컬렉션에 추가
        rig_objects = [camera_obj, camera_empty, crane_empty, focus_empty, position_empty, rotation_empty]
        for obj in rig_objects:
            for col in obj.users_collection:
                col.objects.unlink(obj)
            collection.objects.link(obj)
        
        # Drivers 설정 (원본 분석 기준으로 정확히)
        self.setup_drivers(camera_empty, crane_empty, position_empty, rotation_empty, camera_obj)
        
        # 새 리그를 활성 리그로 설정
        context.scene.rigcam_props.active_rig = camera_empty.name
        context.scene.camera = camera_obj
        
        force_viewport_update(context)
        self.report({'INFO'}, f"Created rig: {final_name}")
        return {'FINISHED'}
    
    def setup_drivers(self, camera_empty, crane_empty, position_empty, rotation_empty, camera_obj):
        """Driver들 설정 (원본 분석 결과 기준)"""
        
        # Driver #1: Camera location Y ← 8.CAM_POS_Y
        driver = camera_obj.driver_add("location", 1).driver
        var = driver.variables.new()
        var.name = "var"
        var.type = 'SINGLE_PROP'
        var.targets[0].id = camera_empty
        var.targets[0].data_path = '["8.CAM_POS_Y"]'
        driver.expression = "var + 0.0"
        
        # Driver #2: Camera location Z ← 7.CAM_POS_Z
        driver = camera_obj.driver_add("location", 2).driver
        var = driver.variables.new()
        var.name = "var"
        var.type = 'SINGLE_PROP'
        var.targets[0].id = camera_empty
        var.targets[0].data_path = '["7.CAM_POS_Z"]'
        driver.expression = "var + 0.0"
        
        # Driver #3: Crane rotation Y (pitch) ← 3.Crane_ROT_P
        driver = crane_empty.driver_add("rotation_euler", 1).driver
        var = driver.variables.new()
        var.name = "var"
        var.type = 'SINGLE_PROP'
        var.targets[0].id = camera_empty
        var.targets[0].data_path = '["3.Crane_ROT_P"]'
        driver.expression = "-var"
        
        # Driver #4: Crane rotation Z (yaw) ← 2.Crane_ROT_H  
        driver = crane_empty.driver_add("rotation_euler", 2).driver
        var = driver.variables.new()
        var.name = "var"
        var.type = 'SINGLE_PROP'
        var.targets[0].id = camera_empty
        var.targets[0].data_path = '["2.Crane_ROT_H"]'
        driver.expression = "var + 0.0"
        
        # Driver #5,6,7: Crane location X,Y,Z ← 0.Crane_POS_X/Y/Z
        for i, axis in enumerate(['X', 'Y', 'Z']):
            driver = crane_empty.driver_add("location", i).driver
            var = driver.variables.new()
            var.name = "var"
            var.type = 'SINGLE_PROP'
            var.targets[0].id = camera_empty
            var.targets[0].data_path = f'["0.Crane_POS_{axis}"]'
            driver.expression = "var"
        
        # Driver #8: Position location X ← 1.Cam_Distance
        driver = position_empty.driver_add("location", 0).driver
        var = driver.variables.new()
        var.name = "var"
        var.type = 'SINGLE_PROP'
        var.targets[0].id = camera_empty
        var.targets[0].data_path = '["1.Cam_Distance"]'
        driver.expression = "var + 0.0"
        
        # Driver #9: Position location Z ← 7.CAM_POS_Z
        driver = position_empty.driver_add("location", 2).driver
        var = driver.variables.new()
        var.name = "var"
        var.type = 'SINGLE_PROP'
        var.targets[0].id = camera_empty
        var.targets[0].data_path = '["7.CAM_POS_Z"]'
        driver.expression = "var + 0.0"
        
        # Driver #10,11,12: Rotation rotation Z,Y,X ← 4,5,6.CAM_ROT_H/P/B
        driver = rotation_empty.driver_add("rotation_euler", 2).driver
        var = driver.variables.new()
        var.name = "var"
        var.type = 'SINGLE_PROP'
        var.targets[0].id = camera_empty
        var.targets[0].data_path = '["4.CAM_ROT_H"]'
        driver.expression = "var + 0.0"
        
        driver = rotation_empty.driver_add("rotation_euler", 1).driver
        var = driver.variables.new()
        var.name = "var"
        var.type = 'SINGLE_PROP'
        var.targets[0].id = camera_empty
        var.targets[0].data_path = '["5.CAM_ROT_P"]'
        driver.expression = "var + 0.0"
        
        driver = rotation_empty.driver_add("rotation_euler", 0).driver
        var = driver.variables.new()
        var.name = "var"
        var.type = 'SINGLE_PROP'
        var.targets[0].id = camera_empty
        var.targets[0].data_path = '["6.CAM_ROT_B"]'
        driver.expression = "var + 0.0"
        
        # 카메라 Focal Length Driver 추가
        driver = camera_obj.data.driver_add("lens").driver
        var = driver.variables.new()
        var.name = "var"
        var.type = 'SINGLE_PROP'
        var.targets[0].id = camera_empty
        var.targets[0].data_path = '["9.Focal_lenght"]'
        driver.expression = "var"
        
        # DOF Distance Driver 추가
        driver = camera_obj.data.driver_add("dof.focus_distance").driver
        var = driver.variables.new()
        var.name = "var"
        var.type = 'SINGLE_PROP'
        var.targets[0].id = camera_empty
        var.targets[0].data_path = '["92.FocusDist"]'
        driver.expression = "var"
        
        # F-Stop Driver 추가
        driver = camera_obj.data.driver_add("dof.aperture_fstop").driver
        var = driver.variables.new()
        var.name = "var"
        var.type = 'SINGLE_PROP'
        var.targets[0].id = camera_empty
        var.targets[0].data_path = '["9.F_stop"]'
        driver.expression = "var"
        
        # Clip Start Driver 추가
        driver = camera_obj.data.driver_add("clip_start").driver
        var = driver.variables.new()
        var.name = "var"
        var.type = 'SINGLE_PROP'
        var.targets[0].id = camera_empty
        var.targets[0].data_path = '["9.ClipStartDist"]'
        driver.expression = "var"
        
        # DOF Enable Driver 추가
        driver = camera_obj.data.driver_add("dof.use_dof").driver
        var = driver.variables.new()
        var.name = "var"
        var.type = 'SINGLE_PROP'
        var.targets[0].id = camera_empty
        var.targets[0].data_path = '["Z.DOF"]'
        driver.expression = "var"
    
    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)


class RIGCAM_OT_switch_camera(Operator):
    """리그캠 전환"""
    bl_idname = "rigcam.switch_camera"
    bl_label = "Switch to Rig Cam"
    bl_description = "Switch active camera and set as current rig"
    
    rig_name: StringProperty()
    
    def execute(self, context):
        if self.rig_name == "CAMERA":
            camera_name = "Camera"
        else:
            camera_name = self.rig_name.replace("_CAMERA", "_Camera")
        
        camera_obj = bpy.data.objects.get(camera_name)
        
        if not camera_obj:
            self.report({'ERROR'}, f"Camera {camera_name} not found!")
            return {'CANCELLED'}
        
        context.scene.camera = camera_obj
        context.scene.rigcam_props.active_rig = self.rig_name
        
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                for space in area.spaces:
                    if space.type == 'VIEW_3D':
                        space.region_3d.view_perspective = 'CAMERA'
                        break
        
        force_viewport_update(context)
        self.report({'INFO'}, f"Switched to {camera_name}")
        return {'FINISHED'}


class RIGCAM_OT_delete_rig(Operator):
    """리그캠 삭제"""
    bl_idname = "rigcam.delete_rig"
    bl_label = "Delete Rig Cam"
    bl_description = "Delete selected rig cam and all its components"
    
    rig_name: StringProperty()
    
    def execute(self, context):
        if self.rig_name == "CAMERA":
            components = ["Camera", "CAMERA", "Crane", "Focus", "Position", "Rotation"]
            collection_name = "RigCam_RigCam"
        else:
            base_name = self.rig_name.replace("_CAMERA", "")
            components = [
                f"{base_name}_Camera", f"{base_name}_CAMERA", f"{base_name}_Crane", 
                f"{base_name}_Focus", f"{base_name}_Position", f"{base_name}_Rotation"
            ]
            collection_name = f"{base_name}_RigCam"
        
        deleted_count = 0
        for comp_name in components:
            obj = bpy.data.objects.get(comp_name)
            if obj:
                bpy.data.objects.remove(obj, do_unlink=True)
                deleted_count += 1
        
        collection = bpy.data.collections.get(collection_name)
        if collection:
            bpy.data.collections.remove(collection)
        
        if context.scene.rigcam_props.active_rig == self.rig_name:
            remaining_rigs = get_rig_cameras()
            context.scene.rigcam_props.active_rig = remaining_rigs[0] if remaining_rigs else ""
        
        self.report({'INFO'}, f"Deleted rig with {deleted_count} components")
        return {'FINISHED'}
    
    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)


class RIGCAM_OT_snap_cursor(Operator):
    """커서 위치로 크레인 스냅"""
    bl_idname = "rigcam.snap_cursor"
    bl_label = "Snap to Cursor"
    bl_description = "Snap crane position to 3D cursor"
    
    def execute(self, context):
        active_rig = context.scene.rigcam_props.active_rig
        camera_empty = bpy.data.objects.get(active_rig)
        
        if not camera_empty:
            self.report({'ERROR'}, "No active rig camera found!")
            return {'CANCELLED'}
        
        cursor_loc = context.scene.cursor.location
        
        # 드라이버 즉시 업데이트를 위한 개별 프로퍼티 설정
        set_custom_prop_and_update(camera_empty, "0.Crane_POS_X", cursor_loc.x, context)
        set_custom_prop_and_update(camera_empty, "0.Crane_POS_Y", cursor_loc.y, context)
        set_custom_prop_and_update(camera_empty, "0.Crane_POS_Z", cursor_loc.z, context)
        
        self.report({'INFO'}, "Snapped to cursor!")
        return {'FINISHED'}


class RIGCAM_OT_snap_selected(Operator):
    """선택된 오브젝트로 크레인 스냅"""
    bl_idname = "rigcam.snap_selected"
    bl_label = "Snap to Selected"
    bl_description = "Snap crane position to selected object's center of mass"
    
    def execute(self, context):
        if not context.selected_objects:
            self.report({'WARNING'}, "No object selected!")
            return {'CANCELLED'}
        
        active_rig = context.scene.rigcam_props.active_rig
        camera_empty = bpy.data.objects.get(active_rig)
        
        if not camera_empty:
            self.report({'ERROR'}, "No active rig camera found!")
            return {'CANCELLED'}
        
        selected_obj = context.selected_objects[0]
        
        # Fixed bound box calculation using helper function
        try:
            bbox_center = get_object_center(selected_obj)
        except Exception as e:
            self.report({'ERROR'}, f"Failed to calculate object center: {str(e)}")
            return {'CANCELLED'}
        
        # 드라이버 즉시 업데이트를 위한 개별 프로퍼티 설정
        set_custom_prop_and_update(camera_empty, "0.Crane_POS_X", bbox_center.x, context)
        set_custom_prop_and_update(camera_empty, "0.Crane_POS_Y", bbox_center.y, context)
        set_custom_prop_and_update(camera_empty, "0.Crane_POS_Z", bbox_center.z, context)
        
        self.report({'INFO'}, f"Snapped to {selected_obj.name} center!")
        return {'FINISHED'}


class RIGCAM_OT_snap_eyedropper(Operator):
    """아이드로퍼로 위치 선택"""
    bl_idname = "rigcam.snap_eyedropper"
    bl_label = "Eyedropper Snap"
    bl_description = "Use eyedropper to snap crane to clicked location"
    
    def modal(self, context, event):
        context.window.cursor_set('EYEDROPPER')
        
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            # 안전 체크 추가
            if not context.region or not context.region_data:
                self.report({'ERROR'}, "Invalid viewport context!")
                context.window.cursor_set('DEFAULT')
                return {'CANCELLED'}
            
            region = context.region
            rv3d = context.region_data
            
            # rv3d 체크 추가
            if not hasattr(rv3d, 'view_matrix') or rv3d.view_matrix is None:
                self.report({'ERROR'}, "Invalid 3D viewport!")
                context.window.cursor_set('DEFAULT')
                return {'CANCELLED'}
            
            coord = (event.mouse_region_x, event.mouse_region_y)
            
            try:
                from bpy_extras import view3d_utils
                
                view_vector = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
                ray_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
                
                depsgraph = context.evaluated_depsgraph_get()
                result, location, normal, index, obj, matrix = context.scene.ray_cast(depsgraph, ray_origin, view_vector)
                
                if result:
                    active_rig = context.scene.rigcam_props.active_rig
                    camera_empty = bpy.data.objects.get(active_rig)
                    
                    if camera_empty:
                        # 드라이버 즉시 업데이트를 위한 개별 프로퍼티 설정
                        set_custom_prop_and_update(camera_empty, "0.Crane_POS_X", location.x, context)
                        set_custom_prop_and_update(camera_empty, "0.Crane_POS_Y", location.y, context)
                        set_custom_prop_and_update(camera_empty, "0.Crane_POS_Z", location.z, context)
                        
                        self.report({'INFO'}, f"Snapped to {obj.name}!")
                    else:
                        self.report({'ERROR'}, "No active rig camera found!")
                else:
                    self.report({'WARNING'}, "No surface found!")
            except Exception as e:
                self.report({'ERROR'}, f"Raycast failed: {str(e)}")
            
            context.window.cursor_set('DEFAULT')
            return {'FINISHED'}
        
        elif event.type in {'RIGHTMOUSE', 'ESC'}:
            context.window.cursor_set('DEFAULT')
            return {'CANCELLED'}
        
        return {'RUNNING_MODAL'}
    
    def invoke(self, context, event):
        # 3D 뷰포트에서만 실행되도록 체크
        if context.area.type != 'VIEW_3D':
            self.report({'ERROR'}, "Must be used in 3D Viewport!")
            return {'CANCELLED'}
        
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}


class RIGCAM_OT_focus_eyedropper(Operator):
    """Focus Distance 아이드로퍼"""
    bl_idname = "rigcam.focus_eyedropper" 
    bl_label = "Focus Distance Eyedropper"
    bl_description = "Use eyedropper to set focus distance based on clicked location"
    
    def modal(self, context, event):
        context.window.cursor_set('EYEDROPPER')
        
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            # 안전 체크 추가
            if not context.region or not context.region_data:
                self.report({'ERROR'}, "Invalid viewport context!")
                context.window.cursor_set('DEFAULT')
                return {'CANCELLED'}
            
            region = context.region
            rv3d = context.region_data
            
            # rv3d 체크 추가
            if not hasattr(rv3d, 'view_matrix') or rv3d.view_matrix is None:
                self.report({'ERROR'}, "Invalid 3D viewport!")
                context.window.cursor_set('DEFAULT')
                return {'CANCELLED'}
            
            coord = (event.mouse_region_x, event.mouse_region_y)
            
            try:
                from bpy_extras import view3d_utils
                
                view_vector = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
                ray_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
                
                depsgraph = context.evaluated_depsgraph_get()
                result, location, normal, index, obj, matrix = context.scene.ray_cast(depsgraph, ray_origin, view_vector)
                
                if result:
                    active_rig = context.scene.rigcam_props.active_rig
                    camera_empty = bpy.data.objects.get(active_rig)
                    
                    if active_rig == "CAMERA":
                        camera_name = "Camera"
                    else:
                        camera_name = active_rig.replace("_CAMERA", "_Camera")
                    
                    camera_obj = bpy.data.objects.get(camera_name)
                    
                    if camera_empty and camera_obj:
                        distance = (camera_obj.matrix_world.translation - location).length
                        set_custom_prop_and_update(camera_empty, "92.FocusDist", distance, context)
                        
                        self.report({'INFO'}, f"Focus distance: {distance:.2f}m")
                    else:
                        self.report({'ERROR'}, "Camera objects not found!")
                else:
                    self.report({'WARNING'}, "No target found!")
            except Exception as e:
                self.report({'ERROR'}, f"Focus raycast failed: {str(e)}")
            
            context.window.cursor_set('DEFAULT')
            return {'FINISHED'}
        
        elif event.type in {'RIGHTMOUSE', 'ESC'}:
            context.window.cursor_set('DEFAULT')
            return {'CANCELLED'}
        
        return {'RUNNING_MODAL'}
    
    def invoke(self, context, event):
        # 3D 뷰포트에서만 실행되도록 체크
        if context.area.type != 'VIEW_3D':
            self.report({'ERROR'}, "Must be used in 3D Viewport!")
            return {'CANCELLED'}
        
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}


class RIGCAM_OT_focus_snap_selected(Operator):
    """Focus Distance를 선택된 오브젝트까지 거리로 설정"""
    bl_idname = "rigcam.focus_snap_selected"
    bl_label = "Focus to Selected"
    bl_description = "Set focus distance to selected object"
    
    def execute(self, context):
        if not context.selected_objects:
            self.report({'WARNING'}, "No object selected!")
            return {'CANCELLED'}
        
        active_rig = context.scene.rigcam_props.active_rig
        camera_empty = bpy.data.objects.get(active_rig)
        
        if active_rig == "CAMERA":
            camera_name = "Camera"
        else:
            camera_name = active_rig.replace("_CAMERA", "_Camera")
        
        camera_obj = bpy.data.objects.get(camera_name)
        
        if not camera_empty or not camera_obj:
            self.report({'ERROR'}, "Camera objects not found!")
            return {'CANCELLED'}
        
        selected_obj = context.selected_objects[0]
        
        # Fixed bound box calculation using helper function
        try:
            target_location = get_object_center(selected_obj)
        except Exception as e:
            self.report({'ERROR'}, f"Failed to calculate object center: {str(e)}")
            return {'CANCELLED'}
        
        distance = (camera_obj.matrix_world.translation - target_location).length
        set_custom_prop_and_update(camera_empty, "92.FocusDist", distance, context)
        
        self.report({'INFO'}, f"Focus distance set to {distance:.2f}m")
        return {'FINISHED'}


class RIGCAM_OT_focus_snap_cursor(Operator):
    """Focus distance snap to 3D cursor"""
    bl_idname = "rigcam.focus_snap_cursor"
    bl_label = "Focus Snap to Cursor"
    bl_description = "Set focus distance to 3D cursor location"
    
    def execute(self, context):
        active_rig = context.scene.rigcam_props.active_rig
        camera_empty = bpy.data.objects.get(active_rig)
        
        if not camera_empty:
            self.report({'ERROR'}, "No active rig camera found!")
            return {'CANCELLED'}
        
        if active_rig == "CAMERA":
            camera_name = "Camera"
        else:
            camera_name = active_rig.replace("_CAMERA", "_Camera")
        
        camera_obj = bpy.data.objects.get(camera_name)
        
        if not camera_obj:
            self.report({'ERROR'}, "Camera object not found!")
            return {'CANCELLED'}
        
        cursor_loc = context.scene.cursor.location
        distance = (camera_obj.matrix_world.translation - cursor_loc).length
        set_custom_prop_and_update(camera_empty, "92.FocusDist", distance, context)
        
        self.report({'INFO'}, f"Focus distance set to cursor: {distance:.2f}m")
        return {'FINISHED'}


class RIGCAM_OT_keyframe_group(Operator):
    """그룹별 키프레임"""
    bl_idname = "rigcam.keyframe_group"
    bl_label = "Keyframe Group"
    bl_description = "Insert keyframes for property group"
    
    group: StringProperty()
    
    def execute(self, context):
        active_rig = context.scene.rigcam_props.active_rig
        camera_empty = bpy.data.objects.get(active_rig)
        
        if not camera_empty:
            self.report({'ERROR'}, "No active rig camera found!")
            return {'CANCELLED'}
        
        props = context.scene.rigcam_props
        
        group_props = {
            "crane_pos": ["0.Crane_POS_X", "0.Crane_POS_Y", "0.Crane_POS_Z"],
            "crane_rot": ["2.Crane_ROT_H", "3.Crane_ROT_P"],
            "camera": ["1.Cam_Distance", "4.CAM_ROT_H", "5.CAM_ROT_P", "6.CAM_ROT_B", "7.CAM_POS_Z", "8.CAM_POS_Y"],
            "settings": ["9.Focal_lenght", "9.ClipStartDist"],
            "focus": ["92.FocusDist", "9.F_stop"]  # DOF Enable은 제외
        }
        
        lock_props = {
            "crane_pos": props.lock_crane_pos,
            "crane_rot": props.lock_crane_rot, 
            "camera": props.lock_camera,
            "settings": props.lock_settings,
            "focus": props.lock_focus
        }
        
        if lock_props.get(self.group, False):
            self.report({'WARNING'}, f"{self.group} is locked!")
            return {'CANCELLED'}
        
        keyed_props = []
        for prop_key in group_props.get(self.group, []):
            if prop_key in camera_empty:
                camera_empty.keyframe_insert(data_path=f'["{prop_key}"]')
                keyed_props.append(prop_key)
        
        self.report({'INFO'}, f"Keyframed {len(keyed_props)} properties in {self.group}")
        return {'FINISHED'}


class RIGCAM_OT_keyframe_all(Operator):
    """전체 키프레임 (락 제외)"""
    bl_idname = "rigcam.keyframe_all"
    bl_label = "Keyframe All (Unlocked)"
    bl_description = "Insert keyframes for all unlocked properties"
    
    def execute(self, context):
        active_rig = context.scene.rigcam_props.active_rig
        camera_empty = bpy.data.objects.get(active_rig)
        
        if not camera_empty:
            self.report({'ERROR'}, "No active rig camera found!")
            return {'CANCELLED'}
        
        props = context.scene.rigcam_props
        
        all_groups = {
            "crane_pos": (["0.Crane_POS_X", "0.Crane_POS_Y", "0.Crane_POS_Z"], props.lock_crane_pos),
            "crane_rot": (["2.Crane_ROT_H", "3.Crane_ROT_P"], props.lock_crane_rot),
            "camera": (["1.Cam_Distance", "4.CAM_ROT_H", "5.CAM_ROT_P", "6.CAM_ROT_B", "7.CAM_POS_Z", "8.CAM_POS_Y"], props.lock_camera),
            "settings": (["9.Focal_lenght", "9.ClipStartDist"], props.lock_settings),
            "focus": (["92.FocusDist", "9.F_stop"], props.lock_focus)  # DOF Enable 제외
        }
        
        keyed_count = 0
        locked_groups = []
        
        for group_name, (prop_list, is_locked) in all_groups.items():
            if not is_locked:
                for prop_key in prop_list:
                    if prop_key in camera_empty:
                        camera_empty.keyframe_insert(data_path=f'["{prop_key}"]')
                        keyed_count += 1
            else:
                locked_groups.append(group_name)
        
        # DOF Enable은 키프레임하지 않음 (제거)
        
        message = f"Keyframed {keyed_count} properties"
        if locked_groups:
            message += f" (Skipped locked: {', '.join(locked_groups)})"
        
        self.report({'INFO'}, message)
        return {'FINISHED'}


class RIGCAM_OT_save_preset(Operator):
    """프리셋 저장"""
    bl_idname = "rigcam.save_preset"
    bl_label = "Save Preset"
    bl_description = "Save current rig settings as preset"
    
    preset_name: StringProperty(name="Preset Name", default="New Preset")
    
    def execute(self, context):
        active_rig = context.scene.rigcam_props.active_rig
        camera_empty = bpy.data.objects.get(active_rig)
        
        if not camera_empty:
            self.report({'ERROR'}, "No active rig camera found!")
            return {'CANCELLED'}
        
        preset_data = {}
        prop_keys = [
            "0.Crane_POS_X", "0.Crane_POS_Y", "0.Crane_POS_Z",
            "2.Crane_ROT_H", "3.Crane_ROT_P",
            "1.Cam_Distance", "4.CAM_ROT_H", "5.CAM_ROT_P", "6.CAM_ROT_B",
            "7.CAM_POS_Z", "8.CAM_POS_Y",
            "9.Focal_lenght", "9.ClipStartDist", "92.FocusDist", "9.F_stop"
        ]
        
        for key in prop_keys:
            if key in camera_empty:
                preset_data[key] = camera_empty[key]
        
        preset_data["Z.DOF"] = camera_empty.get("Z.DOF", False)
        
        if "rigcam_presets" not in context.scene:
            context.scene["rigcam_presets"] = {}
        
        presets = context.scene.get("rigcam_presets", {})
        presets[self.preset_name] = preset_data
        context.scene["rigcam_presets"] = presets
        
        self.report({'INFO'}, f"Preset '{self.preset_name}' saved!")
        return {'FINISHED'}
    
    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)


class RIGCAM_OT_load_preset(Operator):
    """프리셋 불러오기"""
    bl_idname = "rigcam.load_preset"
    bl_label = "Load Preset"
    bl_description = "Load saved preset"
    
    preset_name: StringProperty()
    
    def execute(self, context):
        active_rig = context.scene.rigcam_props.active_rig
        camera_empty = bpy.data.objects.get(active_rig)
        
        if not camera_empty:
            self.report({'ERROR'}, "No active rig camera found!")
            return {'CANCELLED'}
        
        presets = context.scene.get("rigcam_presets", {})
        if self.preset_name not in presets:
            self.report({'ERROR'}, f"Preset '{self.preset_name}' not found!")
            return {'CANCELLED'}
        
        preset_data = presets[self.preset_name]
        
        # 각 프로퍼티를 개별적으로 설정 (키프레임 되돌림 방지)
        for key, value in preset_data.items():
            set_custom_prop_and_update(camera_empty, key, value, context)
        
        self.report({'INFO'}, f"Preset '{self.preset_name}' loaded!")
        return {'FINISHED'}


class RIGCAM_OT_frame_jump(Operator):
    """프레임 점프"""
    bl_idname = "rigcam.frame_jump"
    bl_label = "Frame Jump"
    bl_description = "Jump frames forward or backward"
    
    direction: StringProperty()  # "forward" or "backward"
    
    def execute(self, context):
        props = context.scene.rigcam_props
        scene = context.scene
        
        # 프레임 점프 수 가져오기
        frame_step = props.frame_jump_step if hasattr(props, 'frame_jump_step') else 10
        
        if self.direction == "forward":
            new_frame = scene.frame_current + frame_step
        else:  # backward
            new_frame = scene.frame_current - frame_step
        
        # 프레임 범위 체크
        new_frame = max(scene.frame_start, min(scene.frame_end, new_frame))
        scene.frame_current = new_frame
        
        self.report({'INFO'}, f"Jumped to frame {new_frame}")
        return {'FINISHED'}


class RIGCAM_OT_edit_fcurve(Operator):
    """F-Curve 편집 창 열기"""
    bl_idname = "rigcam.edit_fcurve"
    bl_label = "Edit F-Curve"
    bl_description = "Open Graph Editor for selected property group (reuses existing window if available)"
    
    group: StringProperty()
    
    def execute(self, context):
        active_rig = context.scene.rigcam_props.active_rig
        camera_empty = bpy.data.objects.get(active_rig)
        
        if not camera_empty:
            self.report({'ERROR'}, "No active rig camera found!")
            return {'CANCELLED'}
        
        # 카메라 컨트롤러 선택
        bpy.ops.object.select_all(action='DESELECT')
        camera_empty.select_set(True)
        context.view_layer.objects.active = camera_empty
        
        # 기존 Graph Editor 창이 있는지 확인
        graph_editor_found = False
        target_window = None
        
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'GRAPH_EDITOR':
                    graph_editor_found = True
                    target_window = window
                    target_area = area
                    break
            if graph_editor_found:
                break
        
        # Graph Editor 창이 없으면 새로 생성
        if not graph_editor_found:
            bpy.ops.wm.window_new()
            target_window = context.window_manager.windows[-1]
            
            for area in target_window.screen.areas:
                if area.type == 'VIEW_3D':
                    area.type = 'GRAPH_EDITOR'
                    target_area = area
                    break
        
        # Graph Editor 설정
        for space in target_area.spaces:
            if space.type == 'GRAPH_EDITOR':
                space.dopesheet.show_only_selected = True
                
                # 그룹별로 해당 프로퍼티만 표시하고 나머지는 숨기기
                group_props = {
                    "crane_pos": ["0.Crane_POS_X", "0.Crane_POS_Y", "0.Crane_POS_Z"],
                    "crane_rot": ["2.Crane_ROT_H", "3.Crane_ROT_P"],
                    "camera": ["1.Cam_Distance", "4.CAM_ROT_H", "5.CAM_ROT_P", "6.CAM_ROT_B", "7.CAM_POS_Z", "8.CAM_POS_Y"],
                    "settings": ["9.Focal_lenght", "9.ClipStartDist"],
                    "focus": ["92.FocusDist", "9.F_stop"]
                }
                
                # 모든 F-Curve 먼저 숨기기
                if camera_empty.animation_data and camera_empty.animation_data.action:
                    action = camera_empty.animation_data.action
                    for fcurve in action.fcurves:
                        fcurve.hide = True
                    
                    # 선택된 그룹의 F-Curve만 표시
                    if self.group in group_props:
                        target_props = group_props[self.group]
                        for fcurve in action.fcurves:
                            for prop in target_props:
                                if f'["{prop}"]' in fcurve.data_path:
                                    fcurve.hide = False
                                    break
                
                break
        
        self.report({'INFO'}, f"F-Curve editor showing {self.group} group")
        return {'FINISHED'}


class RIGCAM_OT_set_frame_step(Operator):
    """프레임 스텝 설정"""
    bl_idname = "rigcam.set_frame_step"
    bl_label = "Set Frame Step"
    bl_description = "Set frame jump step to specified value"
    
    value: IntProperty()
    
    def execute(self, context):
        props = context.scene.rigcam_props
        props.frame_jump_step = self.value
        
        self.report({'INFO'}, f"Frame step set to: {self.value}")
        return {'FINISHED'}


class RIGCAM_OT_increase_frame_step(Operator):
    """프레임 스텝 증가"""
    bl_idname = "rigcam.increase_frame_step"
    bl_label = "Increase Frame Step"
    bl_description = "Increase frame jump step by specified amount"
    
    amount: IntProperty()
    
    def execute(self, context):
        props = context.scene.rigcam_props
        props.frame_jump_step += self.amount
        # 최대값 제한
        if props.frame_jump_step > 1000:
            props.frame_jump_step = 1000
        
        self.report({'INFO'}, f"Frame step: {props.frame_jump_step}")
        return {'FINISHED'}


class RIGCAM_OT_select_camera_ctrl(Operator):
    """카메라 컨트롤러 선택"""
    bl_idname = "rigcam.select_camera_ctrl"
    bl_label = "Select Camera Ctrl"
    bl_description = "Select the active camera controller (CAMERA empty) for timeline editing"
    
    def execute(self, context):
        active_rig = context.scene.rigcam_props.active_rig
        camera_empty = bpy.data.objects.get(active_rig)
        
        if not camera_empty:
            self.report({'ERROR'}, "No active rig camera found!")
            return {'CANCELLED'}
        
        # 모든 오브젝트 선택 해제
        bpy.ops.object.select_all(action='DESELECT')
        
        # 카메라 컨트롤러 선택 및 활성화
        camera_empty.select_set(True)
        context.view_layer.objects.active = camera_empty
        
        self.report({'INFO'}, f"Selected {camera_empty.name} for timeline editing")
        return {'FINISHED'}


class RIGCAM_OT_delete_preset(Operator):
    """프리셋 삭제"""
    bl_idname = "rigcam.delete_preset"
    bl_label = "Delete Preset" 
    bl_description = "Delete selected preset"
    
    preset_name: StringProperty()
    
    def execute(self, context):
        presets = context.scene.get("rigcam_presets", {})
        if self.preset_name in presets:
            del presets[self.preset_name]
            context.scene["rigcam_presets"] = presets
            self.report({'INFO'}, f"Preset '{self.preset_name}' deleted!")
        return {'FINISHED'}
    
    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)


class RIGCAM_PT_main_panel(Panel):
    """통합 Rig Cam 패널"""
    bl_label = "Rig Cam"
    bl_idname = "RIGCAM_PT_main_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Rig Cam"
    
    def draw(self, context):
        layout = self.layout
        props = context.scene.rigcam_props
        
        # Rig Management Section
        management_box = layout.box()
        management_header = management_box.row()
        management_header.label(text="Rig Management", icon='CAMERA_DATA')
        
        management_row = management_header.row(align=True)
        management_row.operator("rigcam.create_rig", text="+ New", icon='ADD')
        
        rig_cameras = get_rig_cameras()
        
        if rig_cameras:
            # Show existing rigs in a compact format
            for rig_name in rig_cameras:
                rig_row = management_box.row(align=True)
                
                if props.active_rig == rig_name:
                    rig_row.label(text="●", icon='RADIOBUT_ON')
                else:
                    rig_row.label(text="○", icon='RADIOBUT_OFF')
                
                switch_op = rig_row.operator("rigcam.switch_camera", text=rig_name.replace("_CAMERA", ""))
                switch_op.rig_name = rig_name
                
                delete_op = rig_row.operator("rigcam.delete_rig", text="", icon='X')
                delete_op.rig_name = rig_name
        
        if not rig_cameras:
            layout.label(text="No rig cameras found!", icon='INFO')
            layout.operator("rigcam.create_rig", text="Create First Rig", icon='ADD')
            return
        
        if not props.active_rig or props.active_rig not in rig_cameras:
            props.active_rig = rig_cameras[0]
        
        camera_empty = bpy.data.objects.get(props.active_rig)
        if not camera_empty:
            layout.label(text="Active rig not found!", icon='ERROR')
            return
        
        layout.separator()
        
        # 크레인 위치
        box = layout.box()
        header = box.row()
        header.label(text="Crane Position", icon='EMPTY_AXIS')
        
        
        # 스냅 버튼들
        snap_row = header.row(align=True)
        
        cursor_btn = snap_row.column()
        cursor_btn.scale_x = 1.5
        cursor_btn.scale_y = 1.5
        cursor_btn.operator("rigcam.snap_cursor", text="", icon='PIVOT_CURSOR')
        
        eyedrop_btn = snap_row.column() 
        eyedrop_btn.scale_x = 1.5
        eyedrop_btn.scale_y = 1.5
        eyedrop_btn.operator("rigcam.snap_eyedropper", text="", icon='EYEDROPPER')
        
        selected_btn = snap_row.column()
        selected_btn.scale_x = 1.5
        selected_btn.scale_y = 1.5
        selected_btn.operator("rigcam.snap_selected", text="", icon='RESTRICT_SELECT_OFF')
        
        # 락, 키프레임, F커브 편집
        controls = header.row(align=True)
        
        lock_btn = controls.column()
        lock_btn.scale_x = 1.5
        lock_btn.scale_y = 1.5
        lock_icon = 'LOCKED' if props.lock_crane_pos else 'UNLOCKED'
        lock_btn.prop(props, "lock_crane_pos", text="", icon=lock_icon)
        
        key_btn = controls.column()
        key_btn.scale_x = 1.5
        key_btn.scale_y = 1.5
        key_op = key_btn.operator("rigcam.keyframe_group", text="", icon='KEY_HLT')
        if key_op:
            key_op.group = "crane_pos"
        
        fcurve_btn = controls.column()
        fcurve_btn.scale_x = 1.5
        fcurve_btn.scale_y = 1.5
        fcurve_op = fcurve_btn.operator("rigcam.edit_fcurve", text="", icon='GRAPH')
        if fcurve_op:
            fcurve_op.group = "crane_pos"
        
        # 값들
        col = box.column(align=True)
        col.enabled = not props.lock_crane_pos
        col.prop(camera_empty, '["0.Crane_POS_X"]', text="X")
        col.prop(camera_empty, '["0.Crane_POS_Y"]', text="Y") 
        col.prop(camera_empty, '["0.Crane_POS_Z"]', text="Z")
        
        # Crane Rotation
        box = layout.box()
        header = box.row()
        header.label(text="Crane Rotation", icon='DRIVER_ROTATIONAL_DIFFERENCE')
        
        controls = header.row(align=True)
        
        lock_btn = controls.column()
        lock_btn.scale_x = 1.5
        lock_btn.scale_y = 1.5
        lock_icon = 'LOCKED' if props.lock_crane_rot else 'UNLOCKED'
        lock_btn.prop(props, "lock_crane_rot", text="", icon=lock_icon)
        
        key_btn = controls.column()
        key_btn.scale_x = 1.5
        key_btn.scale_y = 1.5
        key_op = key_btn.operator("rigcam.keyframe_group", text="", icon='KEY_HLT')
        if key_op:
            key_op.group = "crane_rot"
        
        fcurve_btn = controls.column()
        fcurve_btn.scale_x = 1.5
        fcurve_btn.scale_y = 1.5
        fcurve_op = fcurve_btn.operator("rigcam.edit_fcurve", text="", icon='GRAPH')
        if fcurve_op:
            fcurve_op.group = "crane_rot"
        
        col = box.column(align=True)
        col.enabled = not props.lock_crane_rot
        col.prop(camera_empty, '["2.Crane_ROT_H"]', text="Horizontal")
        col.prop(camera_empty, '["3.Crane_ROT_P"]', text="Pitch")
        
        # Camera Control
        box = layout.box()
        header = box.row()
        header.label(text="Camera Control", icon='CAMERA_DATA')
        
        controls = header.row(align=True)
        
        lock_btn = controls.column()
        lock_btn.scale_x = 1.5
        lock_btn.scale_y = 1.5
        lock_icon = 'LOCKED' if props.lock_camera else 'UNLOCKED'
        lock_btn.prop(props, "lock_camera", text="", icon=lock_icon)
        
        key_btn = controls.column()
        key_btn.scale_x = 1.5
        key_btn.scale_y = 1.5
        key_op = key_btn.operator("rigcam.keyframe_group", text="", icon='KEY_HLT')
        if key_op:
            key_op.group = "camera"
        
        fcurve_btn = controls.column()
        fcurve_btn.scale_x = 1.5
        fcurve_btn.scale_y = 1.5
        fcurve_op = fcurve_btn.operator("rigcam.edit_fcurve", text="", icon='GRAPH')
        if fcurve_op:
            fcurve_op.group = "camera"
        
        col = box.column(align=True)
        col.enabled = not props.lock_camera
        col.prop(camera_empty, '["1.Cam_Distance"]', text="Distance")
        
        row = col.row(align=True)
        row.prop(camera_empty, '["4.CAM_ROT_H"]', text="H")
        row.prop(camera_empty, '["5.CAM_ROT_P"]', text="P")
        row.prop(camera_empty, '["6.CAM_ROT_B"]', text="B")
        
        col.separator()
        row = col.row(align=True)
        row.prop(camera_empty, '["8.CAM_POS_Y"]', text="Y")
        row.prop(camera_empty, '["7.CAM_POS_Z"]', text="Z")
        
        # Camera Settings
        box = layout.box()
        header = box.row()
        header.label(text="Camera Settings", icon='CAMERA_STEREO')
        
        controls = header.row(align=True)
        
        lock_btn = controls.column()
        lock_btn.scale_x = 1.5
        lock_btn.scale_y = 1.5
        lock_icon = 'LOCKED' if props.lock_settings else 'UNLOCKED'
        lock_btn.prop(props, "lock_settings", text="", icon=lock_icon)
        
        key_btn = controls.column()
        key_btn.scale_x = 1.5
        key_btn.scale_y = 1.5
        key_op = key_btn.operator("rigcam.keyframe_group", text="", icon='KEY_HLT')
        if key_op:
            key_op.group = "settings"
        
        fcurve_btn = controls.column()
        fcurve_btn.scale_x = 1.5
        fcurve_btn.scale_y = 1.5
        fcurve_op = fcurve_btn.operator("rigcam.edit_fcurve", text="", icon='GRAPH')
        if fcurve_op:
            fcurve_op.group = "settings"
        
        col = box.column(align=True)
        col.enabled = not props.lock_settings
        col.prop(camera_empty, '["9.Focal_lenght"]', text="Focal Length")
        col.prop(camera_empty, '["9.ClipStartDist"]', text="Clip Start")
        
        # Focus & DOF
        box = layout.box()
        header = box.row()
        header.label(text="Focus & DOF", icon='RESTRICT_SELECT_OFF')
        
        # Focus 아이드로퍼와 선택된 오브젝트 스냅
        focus_row = header.row(align=True)
        
        focus_eyedrop_btn = focus_row.column()
        focus_eyedrop_btn.scale_x = 1.5
        focus_eyedrop_btn.scale_y = 1.5
        focus_eyedrop_btn.operator("rigcam.focus_eyedropper", text="", icon='EYEDROPPER')
        
        focus_snap_btn = focus_row.column()
        focus_snap_btn.scale_x = 1.5
        focus_snap_btn.scale_y = 1.5
        focus_snap_btn.operator("rigcam.focus_snap_selected", text="", icon='RESTRICT_SELECT_OFF')
        
        focus_cursor_btn = focus_row.column()
        focus_cursor_btn.scale_x = 1.5
        focus_cursor_btn.scale_y = 1.5
        focus_cursor_btn.operator("rigcam.focus_snap_cursor", text="", icon='PIVOT_CURSOR')
        
        # 락, 키프레임, F커브 편집
        controls = header.row(align=True)
        
        lock_btn = controls.column()
        lock_btn.scale_x = 1.5
        lock_btn.scale_y = 1.5
        lock_icon = 'LOCKED' if props.lock_focus else 'UNLOCKED'
        lock_btn.prop(props, "lock_focus", text="", icon=lock_icon)
        
        key_btn = controls.column()
        key_btn.scale_x = 1.5
        key_btn.scale_y = 1.5
        key_op = key_btn.operator("rigcam.keyframe_group", text="", icon='KEY_HLT')
        if key_op:
            key_op.group = "focus"
        
        fcurve_btn = controls.column()
        fcurve_btn.scale_x = 1.5
        fcurve_btn.scale_y = 1.5
        fcurve_op = fcurve_btn.operator("rigcam.edit_fcurve", text="", icon='GRAPH')
        if fcurve_op:
            fcurve_op.group = "focus"
        
        col = box.column(align=True)
        col.prop(camera_empty, '["Z.DOF"]', text="Enable DOF")
        
        if camera_empty.get("Z.DOF", False):
            col.enabled = not props.lock_focus
            col.prop(camera_empty, '["92.FocusDist"]', text="Focus Distance")
            col.prop(camera_empty, '["9.F_stop"]', text="F-Stop")
        
        # 프레임 이동 컨트롤
        layout.separator()
        frame_box = layout.box()
        frame_header = frame_box.row()
        frame_header.label(text="Frame Control", icon='TIME')
        
        # 프레임 스텝 입력
        step_input = frame_box.row()
        step_input.prop(props, "frame_jump_step", text="Step")
        
        # Quick Add 버튼들
        step_row = frame_box.row(align=True)
        step_row.label(text="Quick Add:")
        
        add_amounts = [1, 5, 10, 30]
        for amount in add_amounts:
            step_op = step_row.operator("rigcam.increase_frame_step", text=f"+{amount}")
            step_op.amount = amount
        
        # Quick Set 버튼들
        set_row = frame_box.row(align=True)
        set_row.label(text="Quick Set:")
        
        set_values = [1, 5, 10, 30]
        for value in set_values:
            set_op = set_row.operator("rigcam.set_frame_step", text=f"{value}")
            set_op.value = value

        # 프레임 점프 버튼들 (크게)
        jump_row = frame_box.row(align=True)
        jump_row.scale_y = 2.0

        # 뒤로 버튼
        back_btn = jump_row.column()
        back_btn.scale_x = 1.2
        back_btn.scale_y = 1.0
        back_op = back_btn.operator("rigcam.frame_jump", text=f"← -{props.frame_jump_step}", icon='TRIA_LEFT')
        back_op.direction = "backward"

        # 현재 프레임 표시 박스 (가운데)
        frame_display = jump_row.column()
        frame_display.scale_x = 1.0
        frame_box_inner = frame_display.box()
        frame_box_inner.scale_y = 1.0
        frame_label = frame_box_inner.row()
        frame_label.alignment = 'CENTER'
        frame_label.scale_y = 1.0
        frame_label.label(text=f"{context.scene.frame_current}")

        # 앞으로 버튼
        forward_btn = jump_row.column()
        forward_btn.scale_x = 1.2
        forward_btn.scale_y = 1.0
        forward_op = forward_btn.operator("rigcam.frame_jump", text=f"+{props.frame_jump_step} →", icon='TRIA_RIGHT')
        forward_op.direction = "forward"
        
        # 전체 키프레임 버튼
        layout.separator()
        big_key = layout.row()
        big_key.scale_y = 2.0
        big_key.operator("rigcam.keyframe_all", text="🔒 Keyframe All (Unlocked)", icon='KEY_HLT')
        
        # 카메라 컨트롤러 선택 버튼
        select_cam = layout.row()
        select_cam.scale_y = 2.0
        select_cam.operator("rigcam.select_camera_ctrl", text="🎬 Select Camera Ctrl", icon='CAMERA_STEREO')


class RIGCAM_PT_presets(Panel):
    """프리셋 패널"""
    bl_label = "Presets"
    bl_idname = "RIGCAM_PT_presets"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Rig Cam"
    bl_parent_id = "RIGCAM_PT_main_panel"
    bl_options = {'DEFAULT_CLOSED'}
    
    def draw(self, context):
        layout = self.layout
        
        row = layout.row()
        row.operator("rigcam.save_preset", text="Save Preset", icon='FILE_TICK')
        
        presets = context.scene.get("rigcam_presets", {})
        if presets:
            layout.separator()
            layout.label(text="Saved Presets:")
            
            for preset_name in presets.keys():
                row = layout.row(align=True)
                
                load_op = row.operator("rigcam.load_preset", text=preset_name)
                load_op.preset_name = preset_name
                
                delete_op = row.operator("rigcam.delete_preset", text="", icon='X')
                delete_op.preset_name = preset_name


class RIGCAM_PT_properties_panel(Panel):
    """Properties 패널의 Rig Cam"""
    bl_label = "Rig Cam"
    bl_idname = "RIGCAM_PT_properties_panel"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "scene"

    def draw(self, context):
        layout = self.layout
        props = context.scene.rigcam_props

        rig_cameras = get_rig_cameras()
        if not rig_cameras:
            layout.operator("rigcam.create_rig", text="Create Rig Cam", icon='ADD')
            return

        if not props.active_rig or props.active_rig not in rig_cameras:
            props.active_rig = rig_cameras[0]

        # Active Rig 표시 (사이드 패널과 동일)
        row = layout.row()
        row.label(text="Active Rig:")

        nav_row = row.row(align=True)
        nav_row.scale_x = 0.8

        current_idx = rig_cameras.index(props.active_rig) if props.active_rig in rig_cameras else 0

        if len(rig_cameras) > 1:
            prev_idx = (current_idx - 1) % len(rig_cameras)
            prev_op = nav_row.operator("rigcam.switch_camera", text="", icon='TRIA_LEFT')
            prev_op.rig_name = rig_cameras[prev_idx]

        nav_row.label(text=props.active_rig.replace("_CAMERA", ""))

        if len(rig_cameras) > 1:
            next_idx = (current_idx + 1) % len(rig_cameras)
            next_op = nav_row.operator("rigcam.switch_camera", text="", icon='TRIA_RIGHT')
            next_op.rig_name = rig_cameras[next_idx]

        camera_empty = bpy.data.objects.get(props.active_rig)
        if not camera_empty:
            layout.label(text="Active rig not found!", icon='ERROR')
            return

        layout.separator()

        # 크레인 위치 (사이드 패널과 동일한 스타일)
        box = layout.box()
        header = box.row()
        header.label(text="Crane Position", icon='EMPTY_AXIS')


        # 스냅 버튼들
        snap_row = header.row(align=True)

        cursor_btn = snap_row.column()
        cursor_btn.scale_x = 1.5
        cursor_btn.scale_y = 1.5
        cursor_btn.operator("rigcam.snap_cursor", text="", icon='PIVOT_CURSOR')

        eyedrop_btn = snap_row.column()
        eyedrop_btn.scale_x = 1.5
        eyedrop_btn.scale_y = 1.5
        eyedrop_btn.operator("rigcam.snap_eyedropper", text="", icon='EYEDROPPER')

        selected_btn = snap_row.column()
        selected_btn.scale_x = 1.5
        selected_btn.scale_y = 1.5
        selected_btn.operator("rigcam.snap_selected", text="", icon='RESTRICT_SELECT_OFF')

        # 락, 키프레임, F커브 편집
        controls = header.row(align=True)

        lock_btn = controls.column()
        lock_btn.scale_x = 1.5
        lock_btn.scale_y = 1.5
        lock_icon = 'LOCKED' if props.lock_crane_pos else 'UNLOCKED'
        lock_btn.prop(props, "lock_crane_pos", text="", icon=lock_icon)

        key_btn = controls.column()
        key_btn.scale_x = 1.5
        key_btn.scale_y = 1.5
        key_op = key_btn.operator("rigcam.keyframe_group", text="", icon='KEY_HLT')
        if key_op:
            key_op.group = "crane_pos"

        fcurve_btn = controls.column()
        fcurve_btn.scale_x = 1.5
        fcurve_btn.scale_y = 1.5
        fcurve_op = fcurve_btn.operator("rigcam.edit_fcurve", text="", icon='GRAPH')
        if fcurve_op:
            fcurve_op.group = "crane_pos"

        # 값들
        col = box.column(align=True)
        col.enabled = not props.lock_crane_pos
        col.prop(camera_empty, '["0.Crane_POS_X"]', text="X")
        col.prop(camera_empty, '["0.Crane_POS_Y"]', text="Y")
        col.prop(camera_empty, '["0.Crane_POS_Z"]', text="Z")

        # Crane Rotation (사이드 패널과 동일)
        box = layout.box()
        header = box.row()
        header.label(text="Crane Rotation", icon='DRIVER_ROTATIONAL_DIFFERENCE')

        controls = header.row(align=True)

        lock_btn = controls.column()
        lock_btn.scale_x = 1.5
        lock_btn.scale_y = 1.5
        lock_icon = 'LOCKED' if props.lock_crane_rot else 'UNLOCKED'
        lock_btn.prop(props, "lock_crane_rot", text="", icon=lock_icon)

        key_btn = controls.column()
        key_btn.scale_x = 1.5
        key_btn.scale_y = 1.5
        key_op = key_btn.operator("rigcam.keyframe_group", text="", icon='KEY_HLT')
        if key_op:
            key_op.group = "crane_rot"

        fcurve_btn = controls.column()
        fcurve_btn.scale_x = 1.5
        fcurve_btn.scale_y = 1.5
        fcurve_op = fcurve_btn.operator("rigcam.edit_fcurve", text="", icon='GRAPH')
        if fcurve_op:
            fcurve_op.group = "crane_rot"

        col = box.column(align=True)
        col.enabled = not props.lock_crane_rot
        col.prop(camera_empty, '["2.Crane_ROT_H"]', text="Horizontal")
        col.prop(camera_empty, '["3.Crane_ROT_P"]', text="Pitch")

        # Camera Control (사이드 패널과 동일)
        box = layout.box()
        header = box.row()
        header.label(text="Camera Control", icon='CAMERA_DATA')

        controls = header.row(align=True)

        lock_btn = controls.column()
        lock_btn.scale_x = 1.5
        lock_btn.scale_y = 1.5
        lock_icon = 'LOCKED' if props.lock_camera else 'UNLOCKED'
        lock_btn.prop(props, "lock_camera", text="", icon=lock_icon)

        key_btn = controls.column()
        key_btn.scale_x = 1.5
        key_btn.scale_y = 1.5
        key_op = key_btn.operator("rigcam.keyframe_group", text="", icon='KEY_HLT')
        if key_op:
            key_op.group = "camera"

        fcurve_btn = controls.column()
        fcurve_btn.scale_x = 1.5
        fcurve_btn.scale_y = 1.5
        fcurve_op = fcurve_btn.operator("rigcam.edit_fcurve", text="", icon='GRAPH')
        if fcurve_op:
            fcurve_op.group = "camera"

        col = box.column(align=True)
        col.enabled = not props.lock_camera
        col.prop(camera_empty, '["1.Cam_Distance"]', text="Distance")

        row = col.row(align=True)
        row.prop(camera_empty, '["4.CAM_ROT_H"]', text="H")
        row.prop(camera_empty, '["5.CAM_ROT_P"]', text="P")
        row.prop(camera_empty, '["6.CAM_ROT_B"]', text="B")

        col.separator()
        row = col.row(align=True)
        row.prop(camera_empty, '["8.CAM_POS_Y"]', text="Y")
        row.prop(camera_empty, '["7.CAM_POS_Z"]', text="Z")

        # Camera Settings (사이드 패널과 동일)
        box = layout.box()
        header = box.row()
        header.label(text="Camera Settings", icon='CAMERA_STEREO')

        controls = header.row(align=True)

        lock_btn = controls.column()
        lock_btn.scale_x = 1.5
        lock_btn.scale_y = 1.5
        lock_icon = 'LOCKED' if props.lock_settings else 'UNLOCKED'
        lock_btn.prop(props, "lock_settings", text="", icon=lock_icon)

        key_btn = controls.column()
        key_btn.scale_x = 1.5
        key_btn.scale_y = 1.5
        key_op = key_btn.operator("rigcam.keyframe_group", text="", icon='KEY_HLT')
        if key_op:
            key_op.group = "settings"

        fcurve_btn = controls.column()
        fcurve_btn.scale_x = 1.5
        fcurve_btn.scale_y = 1.5
        fcurve_op = fcurve_btn.operator("rigcam.edit_fcurve", text="", icon='GRAPH')
        if fcurve_op:
            fcurve_op.group = "settings"

        col = box.column(align=True)
        col.enabled = not props.lock_settings
        col.prop(camera_empty, '["9.Focal_lenght"]', text="Focal Length")
        col.prop(camera_empty, '["9.ClipStartDist"]', text="Clip Start")

        # Focus & DOF (사이드 패널과 동일)
        box = layout.box()
        header = box.row()
        header.label(text="Focus & DOF", icon='RESTRICT_SELECT_OFF')

        # Focus 아이드로퍼와 선택된 오브젝트 스냅
        focus_row = header.row(align=True)

        focus_eyedrop_btn = focus_row.column()
        focus_eyedrop_btn.scale_x = 1.5
        focus_eyedrop_btn.scale_y = 1.5
        focus_eyedrop_btn.operator("rigcam.focus_eyedropper", text="", icon='EYEDROPPER')

        focus_snap_btn = focus_row.column()
        focus_snap_btn.scale_x = 1.5
        focus_snap_btn.scale_y = 1.5
        focus_snap_btn.operator("rigcam.focus_snap_selected", text="", icon='RESTRICT_SELECT_OFF')
        
        focus_cursor_btn = focus_row.column()
        focus_cursor_btn.scale_x = 1.5
        focus_cursor_btn.scale_y = 1.5
        focus_cursor_btn.operator("rigcam.focus_snap_cursor", text="", icon='PIVOT_CURSOR')

        # 락, 키프레임, F커브 편집
        controls = header.row(align=True)

        lock_btn = controls.column()
        lock_btn.scale_x = 1.5
        lock_btn.scale_y = 1.5
        lock_icon = 'LOCKED' if props.lock_focus else 'UNLOCKED'
        lock_btn.prop(props, "lock_focus", text="", icon=lock_icon)

        key_btn = controls.column()
        key_btn.scale_x = 1.5
        key_btn.scale_y = 1.5
        key_op = key_btn.operator("rigcam.keyframe_group", text="", icon='KEY_HLT')
        if key_op:
            key_op.group = "focus"

        fcurve_btn = controls.column()
        fcurve_btn.scale_x = 1.5
        fcurve_btn.scale_y = 1.5
        fcurve_op = fcurve_btn.operator("rigcam.edit_fcurve", text="", icon='GRAPH')
        if fcurve_op:
            fcurve_op.group = "focus"

        col = box.column(align=True)
        col.prop(camera_empty, '["Z.DOF"]', text="Enable DOF")

        if camera_empty.get("Z.DOF", False):
            col.enabled = not props.lock_focus
            col.prop(camera_empty, '["92.FocusDist"]', text="Focus Distance")
            col.prop(camera_empty, '["9.F_stop"]', text="F-Stop")

        # 프레임 이동 컨트롤 (사이드 패널과 동일)
        layout.separator()
        frame_box = layout.box()
        frame_header = frame_box.row()
        frame_header.label(text="Frame Control", icon='TIME')

        # 프레임 스텝 입력
        step_input = frame_box.row()
        step_input.prop(props, "frame_jump_step", text="Step")

        # Quick Add 버튼들
        step_row = frame_box.row(align=True)
        step_row.label(text="Quick Add:")

        add_amounts = [1, 5, 10, 30]
        for amount in add_amounts:
            step_op = step_row.operator("rigcam.increase_frame_step", text=f"+{amount}")
            step_op.amount = amount

        # Quick Set 버튼들
        set_row = frame_box.row(align=True)
        set_row.label(text="Quick Set:")

        set_values = [1, 5, 10, 30]
        for value in set_values:
            set_op = set_row.operator("rigcam.set_frame_step", text=f"{value}")
            set_op.value = value

        # 프레임 점프 버튼들 (크게) - 업데이트된 버전
        jump_row = frame_box.row(align=True)
        jump_row.scale_y = 2.0

        # 뒤로 버튼
        back_btn = jump_row.column()
        back_btn.scale_x = 1.2
        back_btn.scale_y = 1.0
        back_op = back_btn.operator("rigcam.frame_jump", text=f"← -{props.frame_jump_step}", icon='TRIA_LEFT')
        back_op.direction = "backward"

        # 현재 프레임 표시 박스 (가운데)
        frame_display = jump_row.column()
        frame_display.scale_x = 1.0
        frame_box_inner = frame_display.box()
        frame_box_inner.scale_y = 1.0
        frame_label = frame_box_inner.row()
        frame_label.alignment = 'CENTER'
        frame_label.scale_y = 1.0
        frame_label.label(text=f"{context.scene.frame_current}")

        # 앞으로 버튼
        forward_btn = jump_row.column()
        forward_btn.scale_x = 1.2
        forward_btn.scale_y = 1.0
        forward_op = forward_btn.operator("rigcam.frame_jump", text=f"+{props.frame_jump_step} →", icon='TRIA_RIGHT')
        forward_op.direction = "forward"

        # 전체 키프레임 버튼 (사이드 패널과 동일)
        layout.separator()
        big_key = layout.row()
        big_key.scale_y = 2.0
        big_key.operator("rigcam.keyframe_all", text="🔒 Keyframe All (Unlocked)", icon='KEY_HLT')

        # 카메라 컨트롤러 선택 버튼 (사이드 패널과 동일)
        select_cam = layout.row()
        select_cam.scale_y = 2.0
        select_cam.operator("rigcam.select_camera_ctrl", text="🎬 Select Camera Ctrl", icon='CAMERA_STEREO')


def register():
    bpy.utils.register_class(RigCamProperties)
    bpy.utils.register_class(RIGCAM_OT_create_rig)
    bpy.utils.register_class(RIGCAM_OT_switch_camera)
    bpy.utils.register_class(RIGCAM_OT_delete_rig)
    bpy.utils.register_class(RIGCAM_OT_snap_cursor)
    bpy.utils.register_class(RIGCAM_OT_snap_selected)
    bpy.utils.register_class(RIGCAM_OT_snap_eyedropper)
    bpy.utils.register_class(RIGCAM_OT_focus_eyedropper)
    bpy.utils.register_class(RIGCAM_OT_focus_snap_selected)
    bpy.utils.register_class(RIGCAM_OT_focus_snap_cursor)
    bpy.utils.register_class(RIGCAM_OT_frame_jump)
    bpy.utils.register_class(RIGCAM_OT_edit_fcurve)
    bpy.utils.register_class(RIGCAM_OT_set_frame_step)
    bpy.utils.register_class(RIGCAM_OT_increase_frame_step)
    bpy.utils.register_class(RIGCAM_OT_select_camera_ctrl)
    bpy.utils.register_class(RIGCAM_OT_keyframe_group)
    bpy.utils.register_class(RIGCAM_OT_keyframe_all)
    bpy.utils.register_class(RIGCAM_OT_save_preset)
    bpy.utils.register_class(RIGCAM_OT_load_preset)
    bpy.utils.register_class(RIGCAM_OT_delete_preset)
    bpy.utils.register_class(RIGCAM_PT_main_panel)
    bpy.utils.register_class(RIGCAM_PT_presets)
    bpy.utils.register_class(RIGCAM_PT_properties_panel)
    
    bpy.types.Scene.rigcam_props = bpy.props.PointerProperty(type=RigCamProperties)


def unregister():
    bpy.utils.unregister_class(RigCamProperties)
    bpy.utils.unregister_class(RIGCAM_OT_create_rig)
    bpy.utils.unregister_class(RIGCAM_OT_switch_camera)
    bpy.utils.unregister_class(RIGCAM_OT_delete_rig)
    bpy.utils.unregister_class(RIGCAM_OT_snap_cursor)
    bpy.utils.unregister_class(RIGCAM_OT_snap_selected)
    bpy.utils.unregister_class(RIGCAM_OT_snap_eyedropper)
    bpy.utils.unregister_class(RIGCAM_OT_focus_eyedropper)
    bpy.utils.unregister_class(RIGCAM_OT_focus_snap_selected)
    bpy.utils.unregister_class(RIGCAM_OT_focus_snap_cursor)
    bpy.utils.unregister_class(RIGCAM_OT_keyframe_group)
    bpy.utils.unregister_class(RIGCAM_OT_keyframe_all)
    bpy.utils.unregister_class(RIGCAM_OT_save_preset)
    bpy.utils.unregister_class(RIGCAM_OT_load_preset)
    bpy.utils.unregister_class(RIGCAM_OT_delete_preset)
    bpy.utils.unregister_class(RIGCAM_PT_main_panel)
    bpy.utils.unregister_class(RIGCAM_PT_presets)
    bpy.utils.unregister_class(RIGCAM_PT_properties_panel)
    
    del bpy.types.Scene.rigcam_props


if __name__ == "__main__":
    register()