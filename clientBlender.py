from mathutils import *
import os
import platform
import ctypes
_STILL_ACTIVE = 259

from .broadcaster.client import Client
import bpy
import bmesh
import struct
import queue
from .broadcaster import common

HOST = "localhost"
PORT = 12800

class ClientBlender(Client):
    def __init__(self, host = HOST, port = PORT):
        super(ClientBlender, self).__init__(host, port)
        self.meshs = {}
        self.meshMapping = {}
        self.cameras = {}
        self.lights = {}

        self.syncObjects = {} # object name / object path
        self.objectNames = {} # object name / object
        self.textures = set()

        self.callbacks = {}
        self.blenderPID = os.getpid()        

    def blenderExists(self):      
        # Hack, check if a window still exists
        try:
            if len(bpy.context.window_manager.windows) == 0:
                return False
        except Exception as e:
            print (e)
            return False
        return True

    def addCallback(self, name, func):
        self.callbacks[name] = func

    def internalUpdate(self):
        meshNames = []
        for meshName in self.meshs:
            meshNames.append(meshName)
        
        self.meshs = {}
        self.meshMapping = {}
        for meshName in meshNames:
            if meshName in bpy.data.meshes:
                meshData = bpy.data.meshes[meshName]
                self.meshs[meshName] = meshData
                self.meshMapping[meshData] = meshName

        cameraNames = []
        for cameraName in self.cameras:
            cameraNames.append(cameraName)
        self.cameras = {}
        for cameraName in cameraNames:
            if cameraName in bpy.data.cameras:
                self.cameras[cameraName] = bpy.data.cameras[cameraName]

        lightNames = []
        for lightName in self.lights:
            lightNames.append(lightName)
        self.lights = {}
        for lightName in lightNames:
            if lightName in bpy.data.lights:
                self.lights[lightName] = bpy.data.lights[lightName]


    # adds object to synchronized object list
    def addSyncObject(self, obj):
        name = obj.name
        self.syncObjects[name] = self.getObjectPath(obj)
        self.objectNames[name] = obj

    # removes object from synchronized object list
    def removeSyncObject(self, obj):
        name = obj.name
        del self.syncObjects[name]
        del self.objectNames[name]

    # returns True if obj is synchronizes
    def isObjectSync(self, obj):
        if obj.name in self.syncObjects:
            return True
        return False

    # returns the path of an object
    def getObjectPath(self,obj):
        path = obj.name
        while obj.parent:
            obj = obj.parent        
            if obj:
                path = obj.name + "/" + path
        return path

    # get first collection
    def getOrCreateCollection(self, name = "Collection"):
        if name not in bpy.data.collections:
            bpy.ops.collection.create(name = name)
            collection = bpy.data.collections[name]
            bpy.context.scene.collection.children.link(collection)
        return bpy.data.collections[name]

    def getOrCreatePath(self, path, collectionName = "Collection"):
        collection = self.getOrCreateCollection(collectionName)
        pathElem = path.split('/')
        parent = None
        ob = None
        for elem in pathElem:
            if elem not in bpy.data.objects:
                ob = bpy.data.objects.new(elem,None)
                collection.objects.link(ob)
            else:
                ob = bpy.data.objects[elem]
            ob.parent = parent
            parent = ob
        if ob:
            self.addSyncObject(ob)
        return ob                    

    def getOrCreateObjectData(self, path, data):
        ob = self.getOrCreatePath(path)
        if not ob:
            return None

        parent = ob.parent
        name = ob.name

        # cannot simply replace objects data
        # needs to destroy and re create it
        self.removeSyncObject(ob)
        bpy.data.objects.remove(ob, do_unlink=True)
        ob = bpy.data.objects.new(name,data)            
        collection = self.getOrCreateCollection()
        collection.objects.link(ob)
        ob.parent = parent
        self.addSyncObject(ob)


    def getOrCreateCamera(self, cameraName):
        if cameraName in self.cameras:
            return self.cameras[cameraName]
        camera = bpy.data.cameras.new(cameraName)
        self.cameras[cameraName] = camera

        return camera

    def buildCamera(self, data):
        cameraPath, start = common.decodeString(data, 0)

        cameraName = cameraPath.split('/')[-1]
        camera = self.getOrCreateCamera(cameraName)

        camera.lens, start = common.decodeFloat(data, start)
        camera.clip_start, start = common.decodeFloat(data, start)
        camera.clip_end, start = common.decodeFloat(data, start)
        camera.dof.aperture_fstop, start = common.decodeFloat(data, start)
        sensorFit, start = common.decodeInt(data, start)
        camera.sensor_width, start = common.decodeFloat(data, start)
        camera.sensor_height, start = common.decodeFloat(data, start)

        if sensorFit == 0:
            camera.sensor_fit = 'AUTO'
        elif sensorFit == 1:
            camera.sensor_fit = 'VERTICAL'
        else:
            camera.sensor_fit = 'HORIZONTAL'

        self.getOrCreateObjectData(cameraPath, camera)

    def getOrCreateLight(self, lightName, lightType):
        if lightName in self.lights:
            return self.lights[lightName]
        light = bpy.data.lights.new(lightName, type = lightType)
        self.lights[lightName] = light
        return light

    def buildLight(self, data):
        lightPath, start = common.decodeString(data, 0)
        lightType, start = common.decodeInt(data, start)
        blighttype = 'POINT'
        if lightType == common.LightType.SUN.value:
            blighttype = 'SUN'
        elif lightType == common.LightType.POINT.value:
            blighttype = 'POINT'
        else:
            blighttype = 'SPOT'

        lightName = lightPath.split('/')[-1]
        light = self.getOrCreateLight(lightName, blighttype)

        shadow, start = common.decodeInt(data, start)
        if shadow is not 0:
            light.use_shadow = True
        else:
            light.use_shadow = False

        color, start = common.decodeColor(data, start)
        light.color = (color[0], color[1], color[2])
        light.energy, start = common.decodeFloat(data, start)
        if lightType == common.LightType.SPOT.value:
            light.spot_size, start = common.decodeFloat(data, start)
            light.spot_blend, start = common.decodeFloat(data, start)

        self.getOrCreateObjectData(lightPath, light)

    def getOrCreateMesh(self, meshName):
        me = None
        if meshName in self.meshs:
            me = self.meshs[meshName]
        else:
            me = bpy.data.meshes.new(meshName)
            self.meshs[meshName] = me
            self.meshMapping[me] = meshName
        return me

    def buildMesh(self, data):
        index = 0
        meshName, index = common.decodeString(data, index)
        positions, index = common.decodeVector3Array(data, index)
        normals, index = common.decodeVector3Array(data, index)
        uvs, index = common.decodeVector2Array(data, index)
        materialIndices, index = common.decodeInt2Array(data, index)
        triangles, index = common.decodeInt3Array(data, index)
        materialNames, index = common.decodeStringArray(data, index)

        bm = bmesh.new()
        verts = []
        for i in range(len(positions)):
            vertex = bm.verts.new(positions[i])
            # according to https://blender.stackexchange.com/questions/49357/bmesh-how-can-i-import-custom-vertex-normals
            # normals are not working for bmesh...
            vertex.normal = normals[i]
            verts.append(vertex)

        uv_layer = None
        if len(uvs) > 0:
            uv_layer = bm.loops.layers.uv.new()

        indexInMaterialIndices = 0
        nextriangleIndex = len(triangles)
        if len(materialIndices) > 1:
            nextriangleIndex = materialIndices[indexInMaterialIndices + 1][0]
        currentMaterialIndex = materialIndices[indexInMaterialIndices][1]

        for i in range(len(triangles)):
            if i >= nextriangleIndex:
                indexInMaterialIndices = indexInMaterialIndices + 1
                nextriangleIndex = len(triangles)
                if len(materialIndices) > indexInMaterialIndices + 1:
                    nextriangleIndex = materialIndices[indexInMaterialIndices + 1][0]
                currentMaterialIndex = materialIndices[indexInMaterialIndices][1]

            triangle = triangles[i]
            i1 = triangle[0]
            i2 = triangle[1]
            i3 = triangle[2]
            try:
                face = bm.faces.new((verts[i1], verts[i2], verts[i3]))
                face.material_index = currentMaterialIndex
                if uv_layer:
                    face.loops[0][uv_layer].uv = uvs[i1]
                    face.loops[1][uv_layer].uv = uvs[i2]
                    face.loops[2][uv_layer].uv = uvs[i3]                
            except:
                pass

        me = self.getOrCreateMesh(meshName)

        bm.to_mesh(me)

        # hack ! Since bmesh cannot be used to set custom normals        
        normals2 = []
        for l in me.loops:
            normals2.append(normals[l.vertex_index])
        me.normals_split_custom_set(normals2)
        me.use_auto_smooth = True

        for materialName in materialNames:
            material = self.getOrCreateMaterial(materialName)
            me.materials.append(material)
        
        bm.free()

    def buildMeshConnection(self, data):
        path, start = common.decodeString(data, 0)
        meshName, start = common.decodeString(data, start)
        mesh = self.meshs[meshName]
        self.getOrCreateObjectData(path, mesh)

    def setTransform(self, obj, position, rotation, scale):
        obj.location = position
        quaternion = (rotation[3], rotation[0], rotation[1], rotation[2])
        if obj.rotation_mode == 'AXIS_ANGLE':
            axisAngle = Quaternion(quaternion).to_axis_angle()
            obj.rotation_axis_angle[0] = axisAngle[1]
            obj.rotation_axis_angle[1] = axisAngle[0][0]
            obj.rotation_axis_angle[2] = axisAngle[0][1]
            obj.rotation_axis_angle[3] = axisAngle[0][2]
        elif obj.rotation_mode == 'QUATERNION':
            obj.rotation_quaternion = quaternion
        else:
            obj.rotation_euler = Quaternion(quaternion).to_euler(obj.rotation_mode)
        obj.scale = scale  

    def buildTransform(self, data):
        start = 0
        objectPath, start = common.decodeString(data, start)                
        position, start = common.decodeVector3(data, start)
        rotation, start = common.decodeVector4(data, start)
        scale, start = common.decodeVector3(data, start)            

        try:
            obj = self.getOrCreatePath(objectPath)
        except KeyError:
            # Object doesn't exist anymore
            return
        if obj:
            self.setTransform(obj, position, rotation, scale)

    def getOrCreateMaterial(self, materialName):
        if materialName in bpy.data.materials:
            material = bpy.data.materials[materialName]
            material.use_nodes = True
            return material

        material = bpy.data.materials.new(name=materialName)   
        material.use_nodes = True 
        return material

    def buildTexture(self, principled, material, channel, isColor, data, index):
        fileName, index = common.decodeString(data, index)
        if len(fileName) > 0:
            texImage = material.node_tree.nodes.new('ShaderNodeTexImage')
            try:
                texImage.image = bpy.data.images.load(fileName)
                if not isColor:
                    texImage.image.colorspace_settings.name = 'Non-Color' 
            except:
                pass
            material.node_tree.links.new(principled.inputs[channel], texImage.outputs['Color'])
        return index


    def buildMaterial(self, data):
        materialNameLength = common.bytesToInt(data[:4])
        start = 4
        end = start + materialNameLength
        materialName = data[start:end].decode()
        start = end

        material = self.getOrCreateMaterial(materialName)
        nodes = material.node_tree.nodes
        # Get a principled node
        principled = None
        if nodes:
            for n in nodes:
                if n.type == 'BSDF_PRINCIPLED':
                    principled = n
                    break

        if not principled:
            print("Cannot find Principled BSDF node")
            return            

        index = start
        
        # Transmission ( 1 - opacity)
        material.transmission, index = common.decodeFloat(data, index)
        material.transmission = 1 - material.transmission
        principled.inputs['Transmission'].default_value = material.transmission
        fileName, index = common.decodeString(data, index)
        if len(fileName) > 0:
            invert = material.node_tree.nodes.new('ShaderNodeInvert')
            material.node_tree.links.new(principled.inputs['Transmission'], invert.outputs['Color'])  
            texImage = material.node_tree.nodes.new('ShaderNodeTexImage')
            try:
                texImage.image = bpy.data.images.load(fileName)
                texImage.image.colorspace_settings.name = 'Non-Color'
            except:
                print ("could not load : " + fileName)
                pass                
            material.node_tree.links.new(invert.inputs['Color'], texImage.outputs['Color'])

        # Base Color
        baseColor, index = common.decodeColor(data, index)
        material.diffuse_color = ( baseColor[0], baseColor[1], baseColor[2], 1)
        principled.inputs['Base Color'].default_value = material.diffuse_color        
        index = self.buildTexture(principled, material, 'Base Color', True, data, index )

        # Metallic
        material.metallic, index = common.decodeFloat(data, index)
        principled.inputs['Metallic'].default_value = material.metallic
        index = self.buildTexture(principled, material, 'Metallic', False, data, index )

        # Roughness
        material.roughness, index = common.decodeFloat(data, index)
        principled.inputs['Roughness'].default_value = material.roughness
        index = self.buildTexture(principled, material, 'Roughness', False, data, index )

        # Normal
        fileName, index = common.decodeString(data, index)
        if len(fileName) > 0:
            normalMap = material.node_tree.nodes.new('ShaderNodeNormalMap')
            material.node_tree.links.new(principled.inputs['Normal'], normalMap.outputs['Normal'])  
            texImage = material.node_tree.nodes.new('ShaderNodeTexImage')
            try:
                texImage.image = bpy.data.images.load(fileName)
                texImage.image.colorspace_settings.name = 'Non-Color'
            except:
                print ("could not load : " + fileName)
                pass                
            material.node_tree.links.new(normalMap.inputs['Color'], texImage.outputs['Color'])

    def buildRename(self,data):
        oldPath, index = common.decodeString(data, 0)
        newPath, index = common.decodeString(data, index)

        oldName = oldPath.split('/')[-1]
        newName = newPath.split('/')[-1]
        obj = self.objectNames[oldName]
        del self.objectNames[oldName]
        del self.syncObjects[oldName]
        obj.name = newName
        self.objectNames[newName] = obj
        self.syncObjects[newName] = newPath

    def buildDuplicate(self, data):
        srcPath, index = common.decodeString(data, 0)
        dstName, index = common.decodeString(data, index)
        dstPosition, index = common.decodeVector3(data, index)
        dstRotation, index = common.decodeVector4(data, index)
        dstScale, index = common.decodeVector3(data, index)

        try:
            obj = self.getOrCreatePath(srcPath)
            newObj = obj.copy()
            newObj.name = dstName
            if hasattr(obj, "data"):
                newObj.data = obj.data.copy()
                newObj.animation_data_clear()
            collection = self.getOrCreateCollection()
            collection.objects.link(newObj)
            self.addSyncObject(newObj)

            self.setTransform(newObj, dstPosition, dstRotation, dstScale)
        except Exception:
            pass

    def buildDelete(self, data):
        path, _ = common.decodeString(data, 0)

        try:
            obj = bpy.data.objects[path.split('/')[-1]]
        except KeyError:
            # Object doesn't exist anymore
            return
        self.removeSyncObject(obj)
        bpy.data.objects.remove(obj, do_unlink=True)

    def buildSendToTrash(self, data):
        path, _ = common.decodeString(data, 0)
        obj = self.getOrCreatePath(path)

        self.removeSyncObject(obj)
        collections = obj.users_collection
        for collection in collections:
            collection.objects.unlink(obj)
        #collection = self.getOrCreateCollection()
        #collection.objects.unlink(obj)
        trashCollection = self.getOrCreateCollection("__Trash__")
        trashCollection.hide_viewport = True
        trashCollection.objects.link(obj)
        
    def buildRestoreFromTrash(self, data):
        name, index = common.decodeString(data, 0)
        path, index = common.decodeString(data, index)

        obj = bpy.data.objects[name]
        trashCollection = self.getOrCreateCollection("__Trash__")
        trashCollection.hide_viewport = True
        trashCollection.objects.unlink(obj)
        collection = self.getOrCreateCollection()
        collection.objects.link(obj)
        if len(path) > 0:
            obj.parent = bpy.data.objects[path.split('/')[-1]]
        
    def getTransformBuffer(self, obj):
        path = self.getObjectPath(obj)
        translate = obj.matrix_local.to_translation()
        quaternion = obj.matrix_local.to_quaternion()
        scale = obj.matrix_local.to_scale()
        return common.encodeString(path) + common.encodeVector3(translate) + common.encodeVector4(quaternion) + common.encodeVector3(scale)

    def sendTransform(self, obj):
        self.addSyncObject(obj)
        transformBuffer = self.getTransformBuffer(obj)
        self.addCommand(common.Command(common.MessageType.TRANSFORM, transformBuffer, 0))

    def buildTextureFile(self, data):
        path, index = common.decodeString(data, 0)
        if not os.path.exists(path):
            size, index = common.decodeInt(data, index)
            try:
                f = open(path, "wb")
                f.write(data[index:index+size])
                f.close()
                self.textures.add(path)
            except:
                print("Could not write : " + path)

    def sendTextureFile(self, path):
        if path in self.textures:
            return
        if os.path.exists(path):
            nameBuffer = common.encodeString(path)
            try:
                f = open(path, "rb")
                data = f.read()
                f.close()
                self.textures.add(path)
                self.addCommand(common.Command(common.MessageType.TEXTURE, nameBuffer + common.encodeInt(len(data)) + data, 0))
            except:
                print ("Could not read : " + path)        

    def getTexture(self, inputs):
        if len(inputs.links) == 1:
            connectedNode = inputs.links[0].from_node
            if type(connectedNode).__name__ == 'ShaderNodeTexImage':
                image = connectedNode.image
                path = bpy.path.abspath(image.filepath)
                self.sendTextureFile(path)
                return path
        return None

    def getMaterialBuffer(self, material):
        name = material.name
        buffer = common.encodeString(name)
        principled = None
        # Get the nodes in the node tree
        if material.node_tree:
            nodes = material.node_tree.nodes
            # Get a principled node            
            if nodes:
                for n in nodes:
                    if n.type == 'BSDF_PRINCIPLED':
                        principled = n
                        break
            #principled = next(n for n in nodes if n.type == 'BSDF_PRINCIPLED')
        if principled is None:
            baseColor = (0.8,0.8,0.8)
            metallic = 0.0
            roughness = 0.5
            opacity = 1.0
            buffer += common.encodeFloat(opacity) + common.encodeString("")
            buffer += common.encodeColor(baseColor) + common.encodeString("")
            buffer += common.encodeFloat(metallic) + common.encodeString("")
            buffer += common.encodeFloat(roughness) + common.encodeString("")
            buffer += common.encodeString("")
        else:
            
            opacityInput = principled.inputs['Transmission']
            opacity = 1.0
            opacityTexture = None
            if len(opacityInput.links) == 1:
                invert = opacityInput.links[0].from_node
                if "Color" in invert.inputs:
                    colorInput = invert.inputs["Color"]
                    opacityTexture = self.getTexture(colorInput)
            else:
                opacity = 1.0 - opacityInput.default_value

            # Get the slot for 'base color'
            baseColorInput = principled.inputs['Base Color'] #Or principled.inputs[0]
            # Get its default value (not the value from a possible link)
            baseColor = baseColorInput.default_value
            baseColorTexture = self.getTexture(baseColorInput)

            metallicInput = principled.inputs['Metallic'] 
            metallic = 0            
            metallicTexture = self.getTexture(metallicInput)
            if len(metallicInput.links) == 0:
                metallic = metallicInput.default_value

            roughnessInput = principled.inputs['Roughness']
            roughness = 1
            roughnessTexture = self.getTexture(roughnessInput)
            if len(roughnessInput.links) == 0:
                roughness = roughnessInput.default_value

            normalInput = principled.inputs['Normal']
            normalTexture = None
            if len(normalInput.links) == 1:
                normalMap = normalInput.links[0].from_node
                if "Color" in normalMap.inputs:
                    colorInput = normalMap.inputs["Color"]
                    normalTexture = self.getTexture(colorInput)
                        
            buffer += common.encodeFloat(opacity)
            if opacityTexture:
                buffer += common.encodeString(opacityTexture)
            else:
                buffer += common.encodeString("")            
            buffer += common.encodeColor(baseColor)
            if baseColorTexture:
                buffer += common.encodeString(baseColorTexture)
            else:
                buffer += common.encodeString("")

            buffer += common.encodeFloat(metallic)
            if metallicTexture:
                buffer += common.encodeString(metallicTexture)
            else:
                buffer += common.encodeString("")

            buffer += common.encodeFloat(roughness)
            if roughnessTexture:
                buffer += common.encodeString(roughnessTexture)
            else:
                buffer += common.encodeString("")

            if normalTexture:
                buffer += common.encodeString(normalTexture)
            else:
                buffer += common.encodeString("")

        return buffer

    def getMaterialBuffers(self, obj):
        try:
            buffers = []
            for slot in obj.material_slots[:]:    
                buffer = getMaterialBuffer(slot.material)
                buffers.append(buffer)
            return buffers
        except:
            print( 'not found' )

    def sendMaterial(self, material):
        self.addCommand(common.Command(common.MessageType.MATERIAL, self.getMaterialBuffer(material), 0))

    def getMeshName(self, mesh):
        meshName = mesh.name
        if mesh in self.meshMapping:
            meshName = self.meshMapping[mesh]
        else:
            self.meshs[meshName] = mesh
            self.meshMapping[mesh] = meshName
        return meshName

    class CurrentBuffers:
        vertices = []
        normals = []
        uvs = []
        indices = []
        materials = []
        materialIndices = []    # array of triangle index, material index

    def getMeshBuffers(self, obj, meshName):
        self.CurrentBuffers.vertices = []
        self.CurrentBuffers.normals = []
        self.CurrentBuffers.uvs = []
        self.CurrentBuffers.indices = []
        self.CurrentBuffers.materials = []
        self.CurrentBuffers.materialIndices = []

        mesh = obj.data

        # compute modifiers
        depsgraph = bpy.context.evaluated_depsgraph_get()
        obj = obj.evaluated_get(depsgraph)
        
        for slot in obj.material_slots[:]: 
            self.CurrentBuffers.materials.append(slot.name.encode())

        # triangulate mesh (before calculating normals)
        mesh = obj.data
        bm = bmesh.new()
        bm.from_mesh(mesh)
        bmesh.ops.triangulate(bm, faces=bm.faces)
        bm.to_mesh(mesh)
        bm.free()

        # Calculate normals, necessary if auto-smooth option enabled
        mesh.calc_normals()
        mesh.calc_normals_split()
        # calc_loop_triangles resets normals so... don't use it
        
        # get uv layer
        uvlayer = mesh.uv_layers.active

        currentMaterialIndex = -1
        currentfaceIndex = 0
        for f in mesh.polygons:
            for loop_id in f.loop_indices:
                index = mesh.loops[loop_id].vertex_index
                self.CurrentBuffers.vertices.extend(mesh.vertices[index].co)
                self.CurrentBuffers.normals.extend(mesh.loops[loop_id].normal)
                if uvlayer:
                    self.CurrentBuffers.uvs.extend([x for x in uvlayer.data[loop_id].uv])
                self.CurrentBuffers.indices.append(loop_id)

            if f.material_index != currentMaterialIndex:
                currentMaterialIndex = f.material_index
                self.CurrentBuffers.materialIndices.append(currentfaceIndex)
                self.CurrentBuffers.materialIndices.append(currentMaterialIndex)
            currentfaceIndex = currentfaceIndex + 1            

        # Vericex count + binary vertices buffer
        size = len(self.CurrentBuffers.vertices) // 3
        binaryVerticesBuffer = common.intToBytes(size,4) + struct.pack(f'{len(self.CurrentBuffers.vertices)}f', *self.CurrentBuffers.vertices)
        # Normals count + binary normals buffer
        size = len(self.CurrentBuffers.normals) // 3
        binaryNormalsBuffer = common.intToBytes(size, 4) + struct.pack(f'{len(self.CurrentBuffers.normals)}f', *self.CurrentBuffers.normals)
        # UVs count + binary uvs buffer
        size = len(self.CurrentBuffers.uvs) // 2
        binaryUVsBuffer = common.intToBytes(size, 4) + struct.pack(f'{len(self.CurrentBuffers.uvs)}f', *self.CurrentBuffers.uvs)
        # material indices + binary material indices buffer
        size = len(self.CurrentBuffers.materialIndices) // 2
        binaryMaterialIndicesBuffer = common.intToBytes(size, 4) + struct.pack(f'{len(self.CurrentBuffers.materialIndices)}I', *self.CurrentBuffers.materialIndices)
        # triangle indices count + binary triangle indices buffer
        size = len(self.CurrentBuffers.indices) // 3
        binaryIndicesBuffer = common.intToBytes(size, 4) + struct.pack(f'{len(self.CurrentBuffers.indices)}I', *self.CurrentBuffers.indices)
        # material names count + binary material bnames buffer
        size = len(self.CurrentBuffers.materials)
        binaryMaterialNames = common.intToBytes(size, 4)
        for material in self.CurrentBuffers.materials:
            binaryMaterialNames += common.intToBytes(len(material),4) + material
        
        return common.encodeString(meshName) + binaryVerticesBuffer + binaryNormalsBuffer + binaryUVsBuffer + binaryMaterialIndicesBuffer + binaryIndicesBuffer + binaryMaterialNames

    def sendMesh(self, obj):
        mesh = obj.data
        meshName = self.getMeshName(mesh)
        meshBuffer = self.getMeshBuffers(obj, meshName)        
        if meshBuffer:
            self.addCommand(common.Command(common.MessageType.MESH, meshBuffer, 0))

    def getMeshConnectionBuffers(self, obj, meshName):
        # geometry path
        path = self.getObjectPath(obj)
        return common.encodeString(path) + common.encodeString(meshName)    

    def sendMeshConnection(self, obj):
        mesh = obj.data
        meshName = self.getMeshName(mesh)
        meshConnectionBuffer = self.getMeshConnectionBuffers(obj, meshName)
        self.addCommand(common.Command(common.MessageType.MESHCONNECTION, meshConnectionBuffer, 0))

    def getCameraBuffer(self, obj):    
        cam = obj.data
        focal = cam.lens
        frontClipPlane = cam.clip_start
        farClipPlane = cam.clip_end
        aperture = cam.dof.aperture_fstop
        sensorFitName = cam.sensor_fit
        sensorFit = common.SensorFitMode.AUTO
        if sensorFitName == 'AUTO':
            sensorFit = common.SensorFitMode.AUTO
        elif sensorFitName == 'HORIZONTAL':
            sensorFit = common.SensorFitMode.HORIZONTAL
        elif sensorFitName == 'VERTICAL':
            sensorFit = common.SensorFitMode.VERTICAL
        sensorWidth = cam.sensor_width
        sensorHeight = cam.sensor_height
        
        path = self.getObjectPath(obj)
        return common.encodeString(path) + \
                common.encodeFloat(focal) + \
                common.encodeFloat(frontClipPlane) + \
                common.encodeFloat(farClipPlane) + \
                common.encodeFloat(aperture) + \
                common.encodeInt(sensorFit.value) + \
                common.encodeFloat(sensorWidth) + \
                common.encodeFloat(sensorHeight)

    def sendCamera(self, obj):
        cameraBuffer = self.getCameraBuffer(obj)
        if cameraBuffer:
            self.addCommand(common.Command(common.MessageType.CAMERA, cameraBuffer, 0))

    def getLightBuffer(self, obj):    
        light = obj.data
        lightTypeName = light.type
        lightType = common.LightType.SUN
        if lightTypeName == 'POINT':
            lightType = common.LightType.POINT
        elif lightTypeName == 'SPOT':
            lightType = common.LightType.SPOT
        elif lightTypeName == 'SUN':
            lightType = common.LightType.SUN
        else:
            return None
        color = light.color
        power = light.energy
        if bpy.context.scene.render.engine == 'CYCLES':
            shadow = light.cycles.cast_shadow
        else:
            shadow = light.use_shadow
        
        spotBlend = 10.0
        spotSize = 0.0
        if lightType == common.LightType.SPOT:
            spotSize = light.spot_size
            spotBlend = light.spot_blend
        
        return common.encodeString(self.getObjectPath(obj)) + \
            common.encodeInt(lightType.value) + \
            common.encodeInt(shadow) + \
            common.encodeColor(color) + \
            common.encodeFloat(power) + \
            common.encodeFloat(spotSize) + \
            common.encodeFloat(spotBlend)

    def sendLight(self, obj):
        lightBuffer = self.getLightBuffer(obj)
        if lightBuffer:
            self.addCommand(common.Command(common.MessageType.LIGHT, lightBuffer, 0))          

    def sendDeletedRenamedReparentedObjects(self):
        # manage objects deleted
        objects = {}        
        for objName in self.objectNames:
            objects[self.objectNames[objName]] = objName

        for obj in bpy.data.objects:
            if obj in objects:
                del objects[obj]

        for obj in objects:
            name = objects[obj]
            self.sendDelete(self.syncObjects[name])
            del self.objectNames[name]
            del self.syncObjects[name]
            print ("Deleted " + name)

        # manage rename
        for objName in self.objectNames:
            obj = self.objectNames[objName]
            if self.getObjectPath(obj) != self.syncObjects[objName]:
                oldName = objName
                newName = obj.name
                if oldName != newName:
                    print ("Renamed " + oldName + " " + newName)
                    oldPath = self.syncObjects[oldName]
                    newPath = self.getObjectPath(obj)
                    del self.syncObjects[oldName]
                    del self.objectNames[oldName]
                    self.syncObjects[newName] = newPath
                    self.objectNames[newName] = obj
                    self.sendRename(oldPath, newPath)

    def getRenameBuffer(self, oldPath, newPath):
        encodedOldPath = oldPath.encode()
        encodedNewPath = newPath.encode()
        buffer = common.intToBytes(len(encodedOldPath),4) + encodedOldPath + common.intToBytes(len(encodedNewPath),4) + encodedNewPath
        return buffer

    def sendRename(self, oldPath, newPath):
        self.addCommand(common.Command(common.MessageType.RENAME, self.getRenameBuffer(oldPath, newPath), 0))
    
    def getDeleteBuffer(self, name):
        encodedName = name.encode()
        buffer = common.intToBytes(len(encodedName),4) + encodedName
        return buffer

    def sendDelete(self, objPath):
        self.addCommand(common.Command(common.MessageType.DELETE, self.getDeleteBuffer(objPath), 0))

    def sendListRooms(self):
        self.addCommand(common.Command(common.MessageType.LIST_ROOMS))

    def buildListRooms(self, data):
        rooms, _ = common.decodeStringArray(data, 0)
        if 'roomsList' in self.callbacks:
            self.callbacks['roomsList'](rooms)

    def sendSceneContent(self):
        if 'SendContent' in self.callbacks:
            self.callbacks['SendContent']()

    def clearContent(self):
        if 'ClearContent' in self.callbacks:
            self.callbacks['ClearContent']()

    def networkConsumer(self):
        while True:
            try:
                command = self.receivedCommands.get_nowait()
            except queue.Empty:
                return 0.01
            else:
                self.receivedCommandsProcessed = True

                if command.type == common.MessageType.LIST_ROOMS:
                    self.buildListRooms(command.data)
                    self.receivedCommandsProcessed = False
                elif command.type == common.MessageType.CONTENT:
                    self.sendSceneContent()
                    self.receivedCommandsProcessed = False
                elif command.type == common.MessageType.CLEAR_CONTENT:
                    self.clearContent()
                elif command.type == common.MessageType.MESH:
                    self.buildMesh(command.data)
                elif command.type == common.MessageType.MESHCONNECTION:
                    self.buildMeshConnection(command.data)
                elif command.type == common.MessageType.TRANSFORM:
                    self.buildTransform(command.data)
                elif command.type == common.MessageType.MATERIAL:
                    self.buildMaterial(command.data)
                elif command.type == common.MessageType.DELETE:
                    self.buildDelete(command.data)
                elif command.type == common.MessageType.CAMERA:
                    self.buildCamera(command.data)
                elif command.type == common.MessageType.LIGHT:
                    self.buildLight(command.data)
                elif command.type == common.MessageType.RENAME:
                    self.buildRename(command.data)
                elif command.type == common.MessageType.DUPLICATE:
                    self.buildDuplicate(command.data)
                elif command.type == common.MessageType.SEND_TO_TRASH:
                    self.buildSendToTrash(command.data)
                elif command.type == common.MessageType.RESTORE_FROM_TRASH:
                    self.buildRestoreFromTrash(command.data)
                elif command.type == common.MessageType.TEXTURE:
                    self.buildTextureFile(command.data)

                self.receivedCommands.task_done()
          