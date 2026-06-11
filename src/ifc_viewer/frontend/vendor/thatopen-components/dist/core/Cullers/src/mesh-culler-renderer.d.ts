import * as THREE from "three";
import { CullerRenderer } from "./culler-renderer";
import { Components } from "../../Components";
import { Event, World, Disposable } from "../../Types";
/**
 * A renderer to hide/show meshes depending on their visibility from the user's point of view.
 */
export declare class MeshCullerRenderer extends CullerRenderer implements Disposable {
    /**
     * Event triggered when the visibility of meshes is updated.
     * Contains two sets: seen and unseen.
     */
    readonly onViewUpdated: Event<{
        seen: Set<THREE.Mesh>;
        unseen: Set<THREE.Mesh>;
    }>;
    /**
     * Map of color code to THREE.InstancedMesh.
     * Used to keep track of color-coded meshes.
     */
    colorMeshes: Map<string, THREE.InstancedMesh<THREE.BufferGeometry<THREE.NormalBufferAttributes>, THREE.Material | THREE.Material[]>>;
    /**
     * @deprecated use config.threshold instead.
     */
    get threshold(): number;
    /**
     * @deprecated use config.threshold instead.
     */
    set threshold(value: number);
    private _colorCodeMeshMap;
    private _meshIDColorCodeMap;
    private _currentVisibleMeshes;
    private _recentlyHiddenMeshes;
    private readonly _transparentMat;
    constructor(components: Components, world: World);
    /** {@link Disposable.dispose} */
    dispose(): void;
    /**
     * Adds a mesh to the culler. When the mesh is not visibile anymore, it will be removed from the scene. When it's visible again, it will be added to the scene.
     * @param mesh - The mesh to add. It can be a regular THREE.Mesh or an instance of THREE.InstancedMesh.
     */
    add(mesh: THREE.Mesh | THREE.InstancedMesh): void;
    /**
     * Removes a mesh from the culler, so its visibility is not controlled by the culler anymore.
     * When the mesh is removed, it will be hidden from the scene and its color-coded mesh will be destroyed.
     * @param mesh - The mesh to remove. It can be a regular THREE.Mesh or an instance of THREE.InstancedMesh.
     */
    remove(mesh: THREE.Mesh | THREE.InstancedMesh): void;
    /**
     * Updates the given instanced meshes inside the culler. You should use this if you change the count property, e.g. when changing the visibility of fragments.
     *
     * @param meshes - The meshes to update.
     *
     * @returns {void}
     */
    updateInstanced(meshes: Iterable<THREE.InstancedMesh>): void;
    private handleWorkerMessage;
    private getAvailableMaterial;
}
