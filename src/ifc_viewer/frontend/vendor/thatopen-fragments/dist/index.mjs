var __defProp = Object.defineProperty;
var __defNormalProp = (obj, key, value) => key in obj ? __defProp(obj, key, { enumerable: true, configurable: true, writable: true, value }) : obj[key] = value;
var __publicField = (obj, key, value) => {
  __defNormalProp(obj, typeof key !== "symbol" ? key + "" : key, value);
  return value;
};
import * as THREE from "three";
import { BufferAttribute, Vector3, Vector2, Plane, Line3, Triangle, Sphere, Matrix4, Box3, BackSide, DoubleSide, FrontSide, Mesh, Ray, BufferGeometry } from "three";
class FragmentMesh extends THREE.InstancedMesh {
  /**
   * Constructs a new FragmentMesh.
   *
   * @param geometry - The geometry for the mesh. Must be indexed.
   * @param material - The material(s) for the mesh. If a single material is provided, it will be wrapped in an array.
   * @param count - The number of instances to create.
   * @param fragment - The fragment associated with this mesh.
   */
  constructor(geometry, material, count, fragment) {
    super(geometry, material, count);
    /**
     * The fragment associated with this mesh.
     */
    __publicField(this, "fragment");
    /**
     * The materials used by this mesh.
     * If a single material is provided, it will be wrapped in an array.
     */
    __publicField(this, "material");
    /**
     * The geometry used by this mesh.
     * It must be an IndexedGeometry.
     */
    __publicField(this, "geometry");
    if (!Array.isArray(material)) {
      material = [material];
    }
    this.material = material;
    if (!geometry.index) {
      throw new Error("The geometry for fragments must be indexed!");
    }
    this.geometry = geometry;
    this.fragment = fragment;
    const size = geometry.index.count;
    if (!geometry.groups.length) {
      geometry.groups.push({
        start: 0,
        count: size,
        materialIndex: 0
      });
    }
  }
  /**
   * Exports the data of the fragment mesh to a serializable format.
   *
   * @returns An object containing the position, normal, index, groups, materials, matrices, and colors of the fragment mesh.
   */
  exportData() {
    const position = this.geometry.attributes.position.array;
    const normal = this.geometry.attributes.normal.array;
    const index = Array.from(this.geometry.index.array);
    const groups = [];
    for (const group of this.geometry.groups) {
      const index2 = group.materialIndex || 0;
      const { start, count } = group;
      groups.push(start, count, index2);
    }
    const materials = [];
    if (Array.isArray(this.material)) {
      for (const material of this.material) {
        const opacity = material.opacity;
        const transparent = material.transparent ? 1 : 0;
        const color = new THREE.Color(material.color).toArray();
        materials.push(opacity, transparent, ...color);
      }
    }
    const matrices = Array.from(this.instanceMatrix.array);
    let colors;
    if (this.instanceColor !== null) {
      colors = Array.from(this.instanceColor.array);
    } else {
      colors = [];
    }
    return {
      position,
      normal,
      index,
      groups,
      materials,
      matrices,
      colors
    };
  }
  clone(_recursive) {
    throw new Error(
      "Fragment meshes can't be cloned directly. Use mesh.fragment.clone instead!"
    );
  }
}
const CENTER = 0;
const AVERAGE = 1;
const SAH = 2;
const CONTAINED = 2;
const TRIANGLE_INTERSECT_COST = 1.25;
const TRAVERSAL_COST = 1;
const BYTES_PER_NODE = 6 * 4 + 4 + 4;
const IS_LEAFNODE_FLAG = 65535;
const FLOAT32_EPSILON = Math.pow(2, -24);
const SKIP_GENERATION = Symbol("SKIP_GENERATION");
function getVertexCount(geo) {
  return geo.index ? geo.index.count : geo.attributes.position.count;
}
function getTriCount(geo) {
  return getVertexCount(geo) / 3;
}
function getIndexArray(vertexCount, BufferConstructor = ArrayBuffer) {
  if (vertexCount > 65535) {
    return new Uint32Array(new BufferConstructor(4 * vertexCount));
  } else {
    return new Uint16Array(new BufferConstructor(2 * vertexCount));
  }
}
function ensureIndex(geo, options) {
  if (!geo.index) {
    const vertexCount = geo.attributes.position.count;
    const BufferConstructor = options.useSharedArrayBuffer ? SharedArrayBuffer : ArrayBuffer;
    const index = getIndexArray(vertexCount, BufferConstructor);
    geo.setIndex(new BufferAttribute(index, 1));
    for (let i = 0; i < vertexCount; i++) {
      index[i] = i;
    }
  }
}
function getFullGeometryRange(geo) {
  const triCount = getTriCount(geo);
  const drawRange = geo.drawRange;
  const start = drawRange.start / 3;
  const end = (drawRange.start + drawRange.count) / 3;
  const offset = Math.max(0, start);
  const count = Math.min(triCount, end) - offset;
  return [{
    offset: Math.floor(offset),
    count: Math.floor(count)
  }];
}
function getRootIndexRanges(geo) {
  if (!geo.groups || !geo.groups.length) {
    return getFullGeometryRange(geo);
  }
  const ranges = [];
  const rangeBoundaries = /* @__PURE__ */ new Set();
  const drawRange = geo.drawRange;
  const drawRangeStart = drawRange.start / 3;
  const drawRangeEnd = (drawRange.start + drawRange.count) / 3;
  for (const group of geo.groups) {
    const groupStart = group.start / 3;
    const groupEnd = (group.start + group.count) / 3;
    rangeBoundaries.add(Math.max(drawRangeStart, groupStart));
    rangeBoundaries.add(Math.min(drawRangeEnd, groupEnd));
  }
  const sortedBoundaries = Array.from(rangeBoundaries.values()).sort((a, b) => a - b);
  for (let i = 0; i < sortedBoundaries.length - 1; i++) {
    const start = sortedBoundaries[i];
    const end = sortedBoundaries[i + 1];
    ranges.push({
      offset: Math.floor(start),
      count: Math.floor(end - start)
    });
  }
  return ranges;
}
function hasGroupGaps(geometry) {
  if (geometry.groups.length === 0) {
    return false;
  }
  const vertexCount = getTriCount(geometry);
  const groups = getRootIndexRanges(geometry).sort((a, b) => a.offset - b.offset);
  const finalGroup = groups[groups.length - 1];
  finalGroup.count = Math.min(vertexCount - finalGroup.offset, finalGroup.count);
  let total = 0;
  groups.forEach(({ count }) => total += count);
  return vertexCount !== total;
}
function arrayToBox(nodeIndex32, array, target) {
  target.min.x = array[nodeIndex32];
  target.min.y = array[nodeIndex32 + 1];
  target.min.z = array[nodeIndex32 + 2];
  target.max.x = array[nodeIndex32 + 3];
  target.max.y = array[nodeIndex32 + 4];
  target.max.z = array[nodeIndex32 + 5];
  return target;
}
function makeEmptyBounds(target) {
  target[0] = target[1] = target[2] = Infinity;
  target[3] = target[4] = target[5] = -Infinity;
}
function getLongestEdgeIndex(bounds) {
  let splitDimIdx = -1;
  let splitDist = -Infinity;
  for (let i = 0; i < 3; i++) {
    const dist = bounds[i + 3] - bounds[i];
    if (dist > splitDist) {
      splitDist = dist;
      splitDimIdx = i;
    }
  }
  return splitDimIdx;
}
function copyBounds(source, target) {
  target.set(source);
}
function unionBounds(a, b, target) {
  let aVal, bVal;
  for (let d = 0; d < 3; d++) {
    const d3 = d + 3;
    aVal = a[d];
    bVal = b[d];
    target[d] = aVal < bVal ? aVal : bVal;
    aVal = a[d3];
    bVal = b[d3];
    target[d3] = aVal > bVal ? aVal : bVal;
  }
}
function expandByTriangleBounds(startIndex, triangleBounds, bounds) {
  for (let d = 0; d < 3; d++) {
    const tCenter = triangleBounds[startIndex + 2 * d];
    const tHalf = triangleBounds[startIndex + 2 * d + 1];
    const tMin = tCenter - tHalf;
    const tMax = tCenter + tHalf;
    if (tMin < bounds[d]) {
      bounds[d] = tMin;
    }
    if (tMax > bounds[d + 3]) {
      bounds[d + 3] = tMax;
    }
  }
}
function computeSurfaceArea(bounds) {
  const d0 = bounds[3] - bounds[0];
  const d1 = bounds[4] - bounds[1];
  const d2 = bounds[5] - bounds[2];
  return 2 * (d0 * d1 + d1 * d2 + d2 * d0);
}
function getBounds(triangleBounds, offset, count, target, centroidTarget = null) {
  let minx = Infinity;
  let miny = Infinity;
  let minz = Infinity;
  let maxx = -Infinity;
  let maxy = -Infinity;
  let maxz = -Infinity;
  let cminx = Infinity;
  let cminy = Infinity;
  let cminz = Infinity;
  let cmaxx = -Infinity;
  let cmaxy = -Infinity;
  let cmaxz = -Infinity;
  const includeCentroid = centroidTarget !== null;
  for (let i = offset * 6, end = (offset + count) * 6; i < end; i += 6) {
    const cx = triangleBounds[i + 0];
    const hx = triangleBounds[i + 1];
    const lx = cx - hx;
    const rx = cx + hx;
    if (lx < minx)
      minx = lx;
    if (rx > maxx)
      maxx = rx;
    if (includeCentroid && cx < cminx)
      cminx = cx;
    if (includeCentroid && cx > cmaxx)
      cmaxx = cx;
    const cy = triangleBounds[i + 2];
    const hy = triangleBounds[i + 3];
    const ly = cy - hy;
    const ry = cy + hy;
    if (ly < miny)
      miny = ly;
    if (ry > maxy)
      maxy = ry;
    if (includeCentroid && cy < cminy)
      cminy = cy;
    if (includeCentroid && cy > cmaxy)
      cmaxy = cy;
    const cz = triangleBounds[i + 4];
    const hz = triangleBounds[i + 5];
    const lz = cz - hz;
    const rz = cz + hz;
    if (lz < minz)
      minz = lz;
    if (rz > maxz)
      maxz = rz;
    if (includeCentroid && cz < cminz)
      cminz = cz;
    if (includeCentroid && cz > cmaxz)
      cmaxz = cz;
  }
  target[0] = minx;
  target[1] = miny;
  target[2] = minz;
  target[3] = maxx;
  target[4] = maxy;
  target[5] = maxz;
  if (includeCentroid) {
    centroidTarget[0] = cminx;
    centroidTarget[1] = cminy;
    centroidTarget[2] = cminz;
    centroidTarget[3] = cmaxx;
    centroidTarget[4] = cmaxy;
    centroidTarget[5] = cmaxz;
  }
}
function getCentroidBounds(triangleBounds, offset, count, centroidTarget) {
  let cminx = Infinity;
  let cminy = Infinity;
  let cminz = Infinity;
  let cmaxx = -Infinity;
  let cmaxy = -Infinity;
  let cmaxz = -Infinity;
  for (let i = offset * 6, end = (offset + count) * 6; i < end; i += 6) {
    const cx = triangleBounds[i + 0];
    if (cx < cminx)
      cminx = cx;
    if (cx > cmaxx)
      cmaxx = cx;
    const cy = triangleBounds[i + 2];
    if (cy < cminy)
      cminy = cy;
    if (cy > cmaxy)
      cmaxy = cy;
    const cz = triangleBounds[i + 4];
    if (cz < cminz)
      cminz = cz;
    if (cz > cmaxz)
      cmaxz = cz;
  }
  centroidTarget[0] = cminx;
  centroidTarget[1] = cminy;
  centroidTarget[2] = cminz;
  centroidTarget[3] = cmaxx;
  centroidTarget[4] = cmaxy;
  centroidTarget[5] = cmaxz;
}
function computeTriangleBounds(geo, fullBounds) {
  makeEmptyBounds(fullBounds);
  const posAttr = geo.attributes.position;
  const index = geo.index ? geo.index.array : null;
  const triCount = getTriCount(geo);
  const triangleBounds = new Float32Array(triCount * 6);
  const normalized = posAttr.normalized;
  const posArr = posAttr.array;
  const bufferOffset = posAttr.offset || 0;
  let stride = 3;
  if (posAttr.isInterleavedBufferAttribute) {
    stride = posAttr.data.stride;
  }
  const getters = ["getX", "getY", "getZ"];
  for (let tri = 0; tri < triCount; tri++) {
    const tri3 = tri * 3;
    const tri6 = tri * 6;
    let ai = tri3 + 0;
    let bi = tri3 + 1;
    let ci = tri3 + 2;
    if (index) {
      ai = index[ai];
      bi = index[bi];
      ci = index[ci];
    }
    if (!normalized) {
      ai = ai * stride + bufferOffset;
      bi = bi * stride + bufferOffset;
      ci = ci * stride + bufferOffset;
    }
    for (let el = 0; el < 3; el++) {
      let a, b, c;
      if (normalized) {
        a = posAttr[getters[el]](ai);
        b = posAttr[getters[el]](bi);
        c = posAttr[getters[el]](ci);
      } else {
        a = posArr[ai + el];
        b = posArr[bi + el];
        c = posArr[ci + el];
      }
      let min = a;
      if (b < min)
        min = b;
      if (c < min)
        min = c;
      let max = a;
      if (b > max)
        max = b;
      if (c > max)
        max = c;
      const halfExtents = (max - min) / 2;
      const el2 = el * 2;
      triangleBounds[tri6 + el2 + 0] = min + halfExtents;
      triangleBounds[tri6 + el2 + 1] = halfExtents + (Math.abs(min) + halfExtents) * FLOAT32_EPSILON;
      if (min < fullBounds[el])
        fullBounds[el] = min;
      if (max > fullBounds[el + 3])
        fullBounds[el + 3] = max;
    }
  }
  return triangleBounds;
}
const BIN_COUNT = 32;
const binsSort = (a, b) => a.candidate - b.candidate;
const sahBins = new Array(BIN_COUNT).fill().map(() => {
  return {
    count: 0,
    bounds: new Float32Array(6),
    rightCacheBounds: new Float32Array(6),
    leftCacheBounds: new Float32Array(6),
    candidate: 0
  };
});
const leftBounds = new Float32Array(6);
function getOptimalSplit(nodeBoundingData, centroidBoundingData, triangleBounds, offset, count, strategy) {
  let axis = -1;
  let pos = 0;
  if (strategy === CENTER) {
    axis = getLongestEdgeIndex(centroidBoundingData);
    if (axis !== -1) {
      pos = (centroidBoundingData[axis] + centroidBoundingData[axis + 3]) / 2;
    }
  } else if (strategy === AVERAGE) {
    axis = getLongestEdgeIndex(nodeBoundingData);
    if (axis !== -1) {
      pos = getAverage(triangleBounds, offset, count, axis);
    }
  } else if (strategy === SAH) {
    const rootSurfaceArea = computeSurfaceArea(nodeBoundingData);
    let bestCost = TRIANGLE_INTERSECT_COST * count;
    const cStart = offset * 6;
    const cEnd = (offset + count) * 6;
    for (let a = 0; a < 3; a++) {
      const axisLeft = centroidBoundingData[a];
      const axisRight = centroidBoundingData[a + 3];
      const axisLength = axisRight - axisLeft;
      const binWidth = axisLength / BIN_COUNT;
      if (count < BIN_COUNT / 4) {
        const truncatedBins = [...sahBins];
        truncatedBins.length = count;
        let b = 0;
        for (let c = cStart; c < cEnd; c += 6, b++) {
          const bin = truncatedBins[b];
          bin.candidate = triangleBounds[c + 2 * a];
          bin.count = 0;
          const {
            bounds,
            leftCacheBounds,
            rightCacheBounds
          } = bin;
          for (let d = 0; d < 3; d++) {
            rightCacheBounds[d] = Infinity;
            rightCacheBounds[d + 3] = -Infinity;
            leftCacheBounds[d] = Infinity;
            leftCacheBounds[d + 3] = -Infinity;
            bounds[d] = Infinity;
            bounds[d + 3] = -Infinity;
          }
          expandByTriangleBounds(c, triangleBounds, bounds);
        }
        truncatedBins.sort(binsSort);
        let splitCount = count;
        for (let bi = 0; bi < splitCount; bi++) {
          const bin = truncatedBins[bi];
          while (bi + 1 < splitCount && truncatedBins[bi + 1].candidate === bin.candidate) {
            truncatedBins.splice(bi + 1, 1);
            splitCount--;
          }
        }
        for (let c = cStart; c < cEnd; c += 6) {
          const center = triangleBounds[c + 2 * a];
          for (let bi = 0; bi < splitCount; bi++) {
            const bin = truncatedBins[bi];
            if (center >= bin.candidate) {
              expandByTriangleBounds(c, triangleBounds, bin.rightCacheBounds);
            } else {
              expandByTriangleBounds(c, triangleBounds, bin.leftCacheBounds);
              bin.count++;
            }
          }
        }
        for (let bi = 0; bi < splitCount; bi++) {
          const bin = truncatedBins[bi];
          const leftCount = bin.count;
          const rightCount = count - bin.count;
          const leftBounds2 = bin.leftCacheBounds;
          const rightBounds = bin.rightCacheBounds;
          let leftProb = 0;
          if (leftCount !== 0) {
            leftProb = computeSurfaceArea(leftBounds2) / rootSurfaceArea;
          }
          let rightProb = 0;
          if (rightCount !== 0) {
            rightProb = computeSurfaceArea(rightBounds) / rootSurfaceArea;
          }
          const cost = TRAVERSAL_COST + TRIANGLE_INTERSECT_COST * (leftProb * leftCount + rightProb * rightCount);
          if (cost < bestCost) {
            axis = a;
            bestCost = cost;
            pos = bin.candidate;
          }
        }
      } else {
        for (let i = 0; i < BIN_COUNT; i++) {
          const bin = sahBins[i];
          bin.count = 0;
          bin.candidate = axisLeft + binWidth + i * binWidth;
          const bounds = bin.bounds;
          for (let d = 0; d < 3; d++) {
            bounds[d] = Infinity;
            bounds[d + 3] = -Infinity;
          }
        }
        for (let c = cStart; c < cEnd; c += 6) {
          const triCenter = triangleBounds[c + 2 * a];
          const relativeCenter = triCenter - axisLeft;
          let binIndex = ~~(relativeCenter / binWidth);
          if (binIndex >= BIN_COUNT)
            binIndex = BIN_COUNT - 1;
          const bin = sahBins[binIndex];
          bin.count++;
          expandByTriangleBounds(c, triangleBounds, bin.bounds);
        }
        const lastBin = sahBins[BIN_COUNT - 1];
        copyBounds(lastBin.bounds, lastBin.rightCacheBounds);
        for (let i = BIN_COUNT - 2; i >= 0; i--) {
          const bin = sahBins[i];
          const nextBin = sahBins[i + 1];
          unionBounds(bin.bounds, nextBin.rightCacheBounds, bin.rightCacheBounds);
        }
        let leftCount = 0;
        for (let i = 0; i < BIN_COUNT - 1; i++) {
          const bin = sahBins[i];
          const binCount = bin.count;
          const bounds = bin.bounds;
          const nextBin = sahBins[i + 1];
          const rightBounds = nextBin.rightCacheBounds;
          if (binCount !== 0) {
            if (leftCount === 0) {
              copyBounds(bounds, leftBounds);
            } else {
              unionBounds(bounds, leftBounds, leftBounds);
            }
          }
          leftCount += binCount;
          let leftProb = 0;
          let rightProb = 0;
          if (leftCount !== 0) {
            leftProb = computeSurfaceArea(leftBounds) / rootSurfaceArea;
          }
          const rightCount = count - leftCount;
          if (rightCount !== 0) {
            rightProb = computeSurfaceArea(rightBounds) / rootSurfaceArea;
          }
          const cost = TRAVERSAL_COST + TRIANGLE_INTERSECT_COST * (leftProb * leftCount + rightProb * rightCount);
          if (cost < bestCost) {
            axis = a;
            bestCost = cost;
            pos = bin.candidate;
          }
        }
      }
    }
  } else {
    console.warn(`MeshBVH: Invalid build strategy value ${strategy} used.`);
  }
  return { axis, pos };
}
function getAverage(triangleBounds, offset, count, axis) {
  let avg = 0;
  for (let i = offset, end = offset + count; i < end; i++) {
    avg += triangleBounds[i * 6 + axis * 2];
  }
  return avg / count;
}
class MeshBVHNode {
  constructor() {
  }
}
function partition(indirectBuffer, index, triangleBounds, offset, count, split) {
  let left = offset;
  let right = offset + count - 1;
  const pos = split.pos;
  const axisOffset = split.axis * 2;
  while (true) {
    while (left <= right && triangleBounds[left * 6 + axisOffset] < pos) {
      left++;
    }
    while (left <= right && triangleBounds[right * 6 + axisOffset] >= pos) {
      right--;
    }
    if (left < right) {
      for (let i = 0; i < 3; i++) {
        let t0 = index[left * 3 + i];
        index[left * 3 + i] = index[right * 3 + i];
        index[right * 3 + i] = t0;
      }
      for (let i = 0; i < 6; i++) {
        let tb = triangleBounds[left * 6 + i];
        triangleBounds[left * 6 + i] = triangleBounds[right * 6 + i];
        triangleBounds[right * 6 + i] = tb;
      }
      left++;
      right--;
    } else {
      return left;
    }
  }
}
function partition_indirect(indirectBuffer, index, triangleBounds, offset, count, split) {
  let left = offset;
  let right = offset + count - 1;
  const pos = split.pos;
  const axisOffset = split.axis * 2;
  while (true) {
    while (left <= right && triangleBounds[left * 6 + axisOffset] < pos) {
      left++;
    }
    while (left <= right && triangleBounds[right * 6 + axisOffset] >= pos) {
      right--;
    }
    if (left < right) {
      let t = indirectBuffer[left];
      indirectBuffer[left] = indirectBuffer[right];
      indirectBuffer[right] = t;
      for (let i = 0; i < 6; i++) {
        let tb = triangleBounds[left * 6 + i];
        triangleBounds[left * 6 + i] = triangleBounds[right * 6 + i];
        triangleBounds[right * 6 + i] = tb;
      }
      left++;
      right--;
    } else {
      return left;
    }
  }
}
function generateIndirectBuffer(geometry, useSharedArrayBuffer) {
  const triCount = (geometry.index ? geometry.index.count : geometry.attributes.position.count) / 3;
  const useUint32 = triCount > 2 ** 16;
  const byteCount = useUint32 ? 4 : 2;
  const buffer = useSharedArrayBuffer ? new SharedArrayBuffer(triCount * byteCount) : new ArrayBuffer(triCount * byteCount);
  const indirectBuffer = useUint32 ? new Uint32Array(buffer) : new Uint16Array(buffer);
  for (let i = 0, l = indirectBuffer.length; i < l; i++) {
    indirectBuffer[i] = i;
  }
  return indirectBuffer;
}
function buildTree(bvh, options) {
  const geometry = bvh.geometry;
  const indexArray = geometry.index ? geometry.index.array : null;
  const maxDepth = options.maxDepth;
  const verbose = options.verbose;
  const maxLeafTris = options.maxLeafTris;
  const strategy = options.strategy;
  const onProgress = options.onProgress;
  const totalTriangles = getTriCount(geometry);
  const indirectBuffer = bvh._indirectBuffer;
  let reachedMaxDepth = false;
  const fullBounds = new Float32Array(6);
  const cacheCentroidBoundingData = new Float32Array(6);
  const triangleBounds = computeTriangleBounds(geometry, fullBounds);
  const partionFunc = options.indirect ? partition_indirect : partition;
  const roots = [];
  const ranges = options.indirect ? getFullGeometryRange(geometry) : getRootIndexRanges(geometry);
  if (ranges.length === 1) {
    const range = ranges[0];
    const root = new MeshBVHNode();
    root.boundingData = fullBounds;
    getCentroidBounds(triangleBounds, range.offset, range.count, cacheCentroidBoundingData);
    splitNode(root, range.offset, range.count, cacheCentroidBoundingData);
    roots.push(root);
  } else {
    for (let range of ranges) {
      const root = new MeshBVHNode();
      root.boundingData = new Float32Array(6);
      getBounds(triangleBounds, range.offset, range.count, root.boundingData, cacheCentroidBoundingData);
      splitNode(root, range.offset, range.count, cacheCentroidBoundingData);
      roots.push(root);
    }
  }
  return roots;
  function triggerProgress(trianglesProcessed) {
    if (onProgress) {
      onProgress(trianglesProcessed / totalTriangles);
    }
  }
  function splitNode(node, offset, count, centroidBoundingData = null, depth = 0) {
    if (!reachedMaxDepth && depth >= maxDepth) {
      reachedMaxDepth = true;
      if (verbose) {
        console.warn(`MeshBVH: Max depth of ${maxDepth} reached when generating BVH. Consider increasing maxDepth.`);
        console.warn(geometry);
      }
    }
    if (count <= maxLeafTris || depth >= maxDepth) {
      triggerProgress(offset + count);
      node.offset = offset;
      node.count = count;
      return node;
    }
    const split = getOptimalSplit(node.boundingData, centroidBoundingData, triangleBounds, offset, count, strategy);
    if (split.axis === -1) {
      triggerProgress(offset + count);
      node.offset = offset;
      node.count = count;
      return node;
    }
    const splitOffset = partionFunc(indirectBuffer, indexArray, triangleBounds, offset, count, split);
    if (splitOffset === offset || splitOffset === offset + count) {
      triggerProgress(offset + count);
      node.offset = offset;
      node.count = count;
    } else {
      node.splitAxis = split.axis;
      const left = new MeshBVHNode();
      const lstart = offset;
      const lcount = splitOffset - offset;
      node.left = left;
      left.boundingData = new Float32Array(6);
      getBounds(triangleBounds, lstart, lcount, left.boundingData, cacheCentroidBoundingData);
      splitNode(left, lstart, lcount, cacheCentroidBoundingData, depth + 1);
      const right = new MeshBVHNode();
      const rstart = splitOffset;
      const rcount = count - lcount;
      node.right = right;
      right.boundingData = new Float32Array(6);
      getBounds(triangleBounds, rstart, rcount, right.boundingData, cacheCentroidBoundingData);
      splitNode(right, rstart, rcount, cacheCentroidBoundingData, depth + 1);
    }
    return node;
  }
}
function buildPackedTree(bvh, options) {
  const geometry = bvh.geometry;
  if (options.indirect) {
    bvh._indirectBuffer = generateIndirectBuffer(geometry, options.useSharedArrayBuffer);
    if (hasGroupGaps(geometry) && !options.verbose) {
      console.warn(
        'MeshBVH: Provided geometry contains groups that do not fully span the vertex contents while using the "indirect" option. BVH may incorrectly report intersections on unrendered portions of the geometry.'
      );
    }
  }
  if (!bvh._indirectBuffer) {
    ensureIndex(geometry, options);
  }
  const roots = buildTree(bvh, options);
  let float32Array;
  let uint32Array;
  let uint16Array;
  const packedRoots = [];
  const BufferConstructor = options.useSharedArrayBuffer ? SharedArrayBuffer : ArrayBuffer;
  for (let i = 0; i < roots.length; i++) {
    const root = roots[i];
    let nodeCount = countNodes(root);
    const buffer = new BufferConstructor(BYTES_PER_NODE * nodeCount);
    float32Array = new Float32Array(buffer);
    uint32Array = new Uint32Array(buffer);
    uint16Array = new Uint16Array(buffer);
    populateBuffer(0, root);
    packedRoots.push(buffer);
  }
  bvh._roots = packedRoots;
  return;
  function countNodes(node) {
    if (node.count) {
      return 1;
    } else {
      return 1 + countNodes(node.left) + countNodes(node.right);
    }
  }
  function populateBuffer(byteOffset, node) {
    const stride4Offset = byteOffset / 4;
    const stride2Offset = byteOffset / 2;
    const isLeaf = !!node.count;
    const boundingData = node.boundingData;
    for (let i = 0; i < 6; i++) {
      float32Array[stride4Offset + i] = boundingData[i];
    }
    if (isLeaf) {
      const offset = node.offset;
      const count = node.count;
      uint32Array[stride4Offset + 6] = offset;
      uint16Array[stride2Offset + 14] = count;
      uint16Array[stride2Offset + 15] = IS_LEAFNODE_FLAG;
      return byteOffset + BYTES_PER_NODE;
    } else {
      const left = node.left;
      const right = node.right;
      const splitAxis = node.splitAxis;
      let nextUnusedPointer;
      nextUnusedPointer = populateBuffer(byteOffset + BYTES_PER_NODE, left);
      if (nextUnusedPointer / 4 > Math.pow(2, 32)) {
        throw new Error("MeshBVH: Cannot store child pointer greater than 32 bits.");
      }
      uint32Array[stride4Offset + 6] = nextUnusedPointer / 4;
      nextUnusedPointer = populateBuffer(nextUnusedPointer, right);
      uint32Array[stride4Offset + 7] = splitAxis;
      return nextUnusedPointer;
    }
  }
}
class SeparatingAxisBounds {
  constructor() {
    this.min = Infinity;
    this.max = -Infinity;
  }
  setFromPointsField(points, field) {
    let min = Infinity;
    let max = -Infinity;
    for (let i = 0, l = points.length; i < l; i++) {
      const p = points[i];
      const val = p[field];
      min = val < min ? val : min;
      max = val > max ? val : max;
    }
    this.min = min;
    this.max = max;
  }
  setFromPoints(axis, points) {
    let min = Infinity;
    let max = -Infinity;
    for (let i = 0, l = points.length; i < l; i++) {
      const p = points[i];
      const val = axis.dot(p);
      min = val < min ? val : min;
      max = val > max ? val : max;
    }
    this.min = min;
    this.max = max;
  }
  isSeparated(other) {
    return this.min > other.max || other.min > this.max;
  }
}
SeparatingAxisBounds.prototype.setFromBox = function() {
  const p = new Vector3();
  return function setFromBox(axis, box) {
    const boxMin = box.min;
    const boxMax = box.max;
    let min = Infinity;
    let max = -Infinity;
    for (let x = 0; x <= 1; x++) {
      for (let y = 0; y <= 1; y++) {
        for (let z = 0; z <= 1; z++) {
          p.x = boxMin.x * x + boxMax.x * (1 - x);
          p.y = boxMin.y * y + boxMax.y * (1 - y);
          p.z = boxMin.z * z + boxMax.z * (1 - z);
          const val = axis.dot(p);
          min = Math.min(val, min);
          max = Math.max(val, max);
        }
      }
    }
    this.min = min;
    this.max = max;
  };
}();
const closestPointLineToLine = function() {
  const dir1 = new Vector3();
  const dir2 = new Vector3();
  const v02 = new Vector3();
  return function closestPointLineToLine2(l1, l2, result) {
    const v0 = l1.start;
    const v10 = dir1;
    const v2 = l2.start;
    const v32 = dir2;
    v02.subVectors(v0, v2);
    dir1.subVectors(l1.end, l1.start);
    dir2.subVectors(l2.end, l2.start);
    const d0232 = v02.dot(v32);
    const d3210 = v32.dot(v10);
    const d3232 = v32.dot(v32);
    const d0210 = v02.dot(v10);
    const d1010 = v10.dot(v10);
    const denom = d1010 * d3232 - d3210 * d3210;
    let d, d2;
    if (denom !== 0) {
      d = (d0232 * d3210 - d0210 * d3232) / denom;
    } else {
      d = 0;
    }
    d2 = (d0232 + d * d3210) / d3232;
    result.x = d;
    result.y = d2;
  };
}();
const closestPointsSegmentToSegment = function() {
  const paramResult = new Vector2();
  const temp12 = new Vector3();
  const temp22 = new Vector3();
  return function closestPointsSegmentToSegment2(l1, l2, target1, target2) {
    closestPointLineToLine(l1, l2, paramResult);
    let d = paramResult.x;
    let d2 = paramResult.y;
    if (d >= 0 && d <= 1 && d2 >= 0 && d2 <= 1) {
      l1.at(d, target1);
      l2.at(d2, target2);
      return;
    } else if (d >= 0 && d <= 1) {
      if (d2 < 0) {
        l2.at(0, target2);
      } else {
        l2.at(1, target2);
      }
      l1.closestPointToPoint(target2, true, target1);
      return;
    } else if (d2 >= 0 && d2 <= 1) {
      if (d < 0) {
        l1.at(0, target1);
      } else {
        l1.at(1, target1);
      }
      l2.closestPointToPoint(target1, true, target2);
      return;
    } else {
      let p;
      if (d < 0) {
        p = l1.start;
      } else {
        p = l1.end;
      }
      let p2;
      if (d2 < 0) {
        p2 = l2.start;
      } else {
        p2 = l2.end;
      }
      const closestPoint = temp12;
      const closestPoint2 = temp22;
      l1.closestPointToPoint(p2, true, temp12);
      l2.closestPointToPoint(p, true, temp22);
      if (closestPoint.distanceToSquared(p2) <= closestPoint2.distanceToSquared(p)) {
        target1.copy(closestPoint);
        target2.copy(p2);
        return;
      } else {
        target1.copy(p);
        target2.copy(closestPoint2);
        return;
      }
    }
  };
}();
const sphereIntersectTriangle = function() {
  const closestPointTemp = new Vector3();
  const projectedPointTemp = new Vector3();
  const planeTemp = new Plane();
  const lineTemp = new Line3();
  return function sphereIntersectTriangle2(sphere, triangle3) {
    const { radius, center } = sphere;
    const { a, b, c } = triangle3;
    lineTemp.start = a;
    lineTemp.end = b;
    const closestPoint1 = lineTemp.closestPointToPoint(center, true, closestPointTemp);
    if (closestPoint1.distanceTo(center) <= radius)
      return true;
    lineTemp.start = a;
    lineTemp.end = c;
    const closestPoint2 = lineTemp.closestPointToPoint(center, true, closestPointTemp);
    if (closestPoint2.distanceTo(center) <= radius)
      return true;
    lineTemp.start = b;
    lineTemp.end = c;
    const closestPoint3 = lineTemp.closestPointToPoint(center, true, closestPointTemp);
    if (closestPoint3.distanceTo(center) <= radius)
      return true;
    const plane = triangle3.getPlane(planeTemp);
    const dp = Math.abs(plane.distanceToPoint(center));
    if (dp <= radius) {
      const pp = plane.projectPoint(center, projectedPointTemp);
      const cp = triangle3.containsPoint(pp);
      if (cp)
        return true;
    }
    return false;
  };
}();
const ZERO_EPSILON = 1e-15;
function isNearZero(value) {
  return Math.abs(value) < ZERO_EPSILON;
}
class ExtendedTriangle extends Triangle {
  constructor(...args) {
    super(...args);
    this.isExtendedTriangle = true;
    this.satAxes = new Array(4).fill().map(() => new Vector3());
    this.satBounds = new Array(4).fill().map(() => new SeparatingAxisBounds());
    this.points = [this.a, this.b, this.c];
    this.sphere = new Sphere();
    this.plane = new Plane();
    this.needsUpdate = true;
  }
  intersectsSphere(sphere) {
    return sphereIntersectTriangle(sphere, this);
  }
  update() {
    const a = this.a;
    const b = this.b;
    const c = this.c;
    const points = this.points;
    const satAxes = this.satAxes;
    const satBounds = this.satBounds;
    const axis0 = satAxes[0];
    const sab0 = satBounds[0];
    this.getNormal(axis0);
    sab0.setFromPoints(axis0, points);
    const axis1 = satAxes[1];
    const sab1 = satBounds[1];
    axis1.subVectors(a, b);
    sab1.setFromPoints(axis1, points);
    const axis2 = satAxes[2];
    const sab2 = satBounds[2];
    axis2.subVectors(b, c);
    sab2.setFromPoints(axis2, points);
    const axis3 = satAxes[3];
    const sab3 = satBounds[3];
    axis3.subVectors(c, a);
    sab3.setFromPoints(axis3, points);
    this.sphere.setFromPoints(this.points);
    this.plane.setFromNormalAndCoplanarPoint(axis0, a);
    this.needsUpdate = false;
  }
}
ExtendedTriangle.prototype.closestPointToSegment = function() {
  const point1 = new Vector3();
  const point2 = new Vector3();
  const edge = new Line3();
  return function distanceToSegment(segment, target1 = null, target2 = null) {
    const { start, end } = segment;
    const points = this.points;
    let distSq;
    let closestDistanceSq = Infinity;
    for (let i = 0; i < 3; i++) {
      const nexti = (i + 1) % 3;
      edge.start.copy(points[i]);
      edge.end.copy(points[nexti]);
      closestPointsSegmentToSegment(edge, segment, point1, point2);
      distSq = point1.distanceToSquared(point2);
      if (distSq < closestDistanceSq) {
        closestDistanceSq = distSq;
        if (target1)
          target1.copy(point1);
        if (target2)
          target2.copy(point2);
      }
    }
    this.closestPointToPoint(start, point1);
    distSq = start.distanceToSquared(point1);
    if (distSq < closestDistanceSq) {
      closestDistanceSq = distSq;
      if (target1)
        target1.copy(point1);
      if (target2)
        target2.copy(start);
    }
    this.closestPointToPoint(end, point1);
    distSq = end.distanceToSquared(point1);
    if (distSq < closestDistanceSq) {
      closestDistanceSq = distSq;
      if (target1)
        target1.copy(point1);
      if (target2)
        target2.copy(end);
    }
    return Math.sqrt(closestDistanceSq);
  };
}();
ExtendedTriangle.prototype.intersectsTriangle = function() {
  const saTri2 = new ExtendedTriangle();
  const arr1 = new Array(3);
  const arr2 = new Array(3);
  const cachedSatBounds = new SeparatingAxisBounds();
  const cachedSatBounds2 = new SeparatingAxisBounds();
  const cachedAxis = new Vector3();
  const dir = new Vector3();
  const dir1 = new Vector3();
  const dir2 = new Vector3();
  const tempDir = new Vector3();
  const edge = new Line3();
  const edge1 = new Line3();
  const edge2 = new Line3();
  const tempPoint = new Vector3();
  function triIntersectPlane(tri, plane, targetEdge) {
    const points = tri.points;
    let count = 0;
    let startPointIntersection = -1;
    for (let i = 0; i < 3; i++) {
      const { start, end } = edge;
      start.copy(points[i]);
      end.copy(points[(i + 1) % 3]);
      edge.delta(dir);
      const startIntersects = isNearZero(plane.distanceToPoint(start));
      if (isNearZero(plane.normal.dot(dir)) && startIntersects) {
        targetEdge.copy(edge);
        count = 2;
        break;
      }
      const doesIntersect = plane.intersectLine(edge, tempPoint);
      if (!doesIntersect && startIntersects) {
        tempPoint.copy(start);
      }
      if ((doesIntersect || startIntersects) && !isNearZero(tempPoint.distanceTo(end))) {
        if (count <= 1) {
          const point = count === 1 ? targetEdge.start : targetEdge.end;
          point.copy(tempPoint);
          if (startIntersects) {
            startPointIntersection = count;
          }
        } else if (count >= 2) {
          const point = startPointIntersection === 1 ? targetEdge.start : targetEdge.end;
          point.copy(tempPoint);
          count = 2;
          break;
        }
        count++;
        if (count === 2 && startPointIntersection === -1) {
          break;
        }
      }
    }
    return count;
  }
  return function intersectsTriangle(other, target = null, suppressLog = false) {
    if (this.needsUpdate) {
      this.update();
    }
    if (!other.isExtendedTriangle) {
      saTri2.copy(other);
      saTri2.update();
      other = saTri2;
    } else if (other.needsUpdate) {
      other.update();
    }
    const plane1 = this.plane;
    const plane2 = other.plane;
    if (Math.abs(plane1.normal.dot(plane2.normal)) > 1 - 1e-10) {
      const satBounds1 = this.satBounds;
      const satAxes1 = this.satAxes;
      arr2[0] = other.a;
      arr2[1] = other.b;
      arr2[2] = other.c;
      for (let i = 0; i < 4; i++) {
        const sb = satBounds1[i];
        const sa = satAxes1[i];
        cachedSatBounds.setFromPoints(sa, arr2);
        if (sb.isSeparated(cachedSatBounds))
          return false;
      }
      const satBounds2 = other.satBounds;
      const satAxes2 = other.satAxes;
      arr1[0] = this.a;
      arr1[1] = this.b;
      arr1[2] = this.c;
      for (let i = 0; i < 4; i++) {
        const sb = satBounds2[i];
        const sa = satAxes2[i];
        cachedSatBounds.setFromPoints(sa, arr1);
        if (sb.isSeparated(cachedSatBounds))
          return false;
      }
      for (let i = 0; i < 4; i++) {
        const sa1 = satAxes1[i];
        for (let i2 = 0; i2 < 4; i2++) {
          const sa2 = satAxes2[i2];
          cachedAxis.crossVectors(sa1, sa2);
          cachedSatBounds.setFromPoints(cachedAxis, arr1);
          cachedSatBounds2.setFromPoints(cachedAxis, arr2);
          if (cachedSatBounds.isSeparated(cachedSatBounds2))
            return false;
        }
      }
      if (target) {
        if (!suppressLog) {
          console.warn("ExtendedTriangle.intersectsTriangle: Triangles are coplanar which does not support an output edge. Setting edge to 0, 0, 0.");
        }
        target.start.set(0, 0, 0);
        target.end.set(0, 0, 0);
      }
      return true;
    } else {
      const count1 = triIntersectPlane(this, plane2, edge1);
      if (count1 === 1 && other.containsPoint(edge1.end)) {
        if (target) {
          target.start.copy(edge1.end);
          target.end.copy(edge1.end);
        }
        return true;
      } else if (count1 !== 2) {
        return false;
      }
      const count2 = triIntersectPlane(other, plane1, edge2);
      if (count2 === 1 && this.containsPoint(edge2.end)) {
        if (target) {
          target.start.copy(edge2.end);
          target.end.copy(edge2.end);
        }
        return true;
      } else if (count2 !== 2) {
        return false;
      }
      edge1.delta(dir1);
      edge2.delta(dir2);
      if (dir1.dot(dir2) < 0) {
        let tmp = edge2.start;
        edge2.start = edge2.end;
        edge2.end = tmp;
      }
      const s1 = edge1.start.dot(dir1);
      const e1 = edge1.end.dot(dir1);
      const s2 = edge2.start.dot(dir1);
      const e2 = edge2.end.dot(dir1);
      const separated1 = e1 < s2;
      const separated2 = s1 < e2;
      if (s1 !== e2 && s2 !== e1 && separated1 === separated2) {
        return false;
      }
      if (target) {
        tempDir.subVectors(edge1.start, edge2.start);
        if (tempDir.dot(dir1) > 0) {
          target.start.copy(edge1.start);
        } else {
          target.start.copy(edge2.start);
        }
        tempDir.subVectors(edge1.end, edge2.end);
        if (tempDir.dot(dir1) < 0) {
          target.end.copy(edge1.end);
        } else {
          target.end.copy(edge2.end);
        }
      }
      return true;
    }
  };
}();
ExtendedTriangle.prototype.distanceToPoint = function() {
  const target = new Vector3();
  return function distanceToPoint(point) {
    this.closestPointToPoint(point, target);
    return point.distanceTo(target);
  };
}();
ExtendedTriangle.prototype.distanceToTriangle = function() {
  const point = new Vector3();
  const point2 = new Vector3();
  const cornerFields = ["a", "b", "c"];
  const line1 = new Line3();
  const line2 = new Line3();
  return function distanceToTriangle(other, target1 = null, target2 = null) {
    const lineTarget = target1 || target2 ? line1 : null;
    if (this.intersectsTriangle(other, lineTarget)) {
      if (target1 || target2) {
        if (target1)
          lineTarget.getCenter(target1);
        if (target2)
          lineTarget.getCenter(target2);
      }
      return 0;
    }
    let closestDistanceSq = Infinity;
    for (let i = 0; i < 3; i++) {
      let dist;
      const field = cornerFields[i];
      const otherVec = other[field];
      this.closestPointToPoint(otherVec, point);
      dist = otherVec.distanceToSquared(point);
      if (dist < closestDistanceSq) {
        closestDistanceSq = dist;
        if (target1)
          target1.copy(point);
        if (target2)
          target2.copy(otherVec);
      }
      const thisVec = this[field];
      other.closestPointToPoint(thisVec, point);
      dist = thisVec.distanceToSquared(point);
      if (dist < closestDistanceSq) {
        closestDistanceSq = dist;
        if (target1)
          target1.copy(thisVec);
        if (target2)
          target2.copy(point);
      }
    }
    for (let i = 0; i < 3; i++) {
      const f11 = cornerFields[i];
      const f12 = cornerFields[(i + 1) % 3];
      line1.set(this[f11], this[f12]);
      for (let i2 = 0; i2 < 3; i2++) {
        const f21 = cornerFields[i2];
        const f22 = cornerFields[(i2 + 1) % 3];
        line2.set(other[f21], other[f22]);
        closestPointsSegmentToSegment(line1, line2, point, point2);
        const dist = point.distanceToSquared(point2);
        if (dist < closestDistanceSq) {
          closestDistanceSq = dist;
          if (target1)
            target1.copy(point);
          if (target2)
            target2.copy(point2);
        }
      }
    }
    return Math.sqrt(closestDistanceSq);
  };
}();
class OrientedBox {
  constructor(min, max, matrix) {
    this.isOrientedBox = true;
    this.min = new Vector3();
    this.max = new Vector3();
    this.matrix = new Matrix4();
    this.invMatrix = new Matrix4();
    this.points = new Array(8).fill().map(() => new Vector3());
    this.satAxes = new Array(3).fill().map(() => new Vector3());
    this.satBounds = new Array(3).fill().map(() => new SeparatingAxisBounds());
    this.alignedSatBounds = new Array(3).fill().map(() => new SeparatingAxisBounds());
    this.needsUpdate = false;
    if (min)
      this.min.copy(min);
    if (max)
      this.max.copy(max);
    if (matrix)
      this.matrix.copy(matrix);
  }
  set(min, max, matrix) {
    this.min.copy(min);
    this.max.copy(max);
    this.matrix.copy(matrix);
    this.needsUpdate = true;
  }
  copy(other) {
    this.min.copy(other.min);
    this.max.copy(other.max);
    this.matrix.copy(other.matrix);
    this.needsUpdate = true;
  }
}
OrientedBox.prototype.update = /* @__PURE__ */ function() {
  return function update() {
    const matrix = this.matrix;
    const min = this.min;
    const max = this.max;
    const points = this.points;
    for (let x = 0; x <= 1; x++) {
      for (let y = 0; y <= 1; y++) {
        for (let z = 0; z <= 1; z++) {
          const i = (1 << 0) * x | (1 << 1) * y | (1 << 2) * z;
          const v = points[i];
          v.x = x ? max.x : min.x;
          v.y = y ? max.y : min.y;
          v.z = z ? max.z : min.z;
          v.applyMatrix4(matrix);
        }
      }
    }
    const satBounds = this.satBounds;
    const satAxes = this.satAxes;
    const minVec = points[0];
    for (let i = 0; i < 3; i++) {
      const axis = satAxes[i];
      const sb = satBounds[i];
      const index = 1 << i;
      const pi = points[index];
      axis.subVectors(minVec, pi);
      sb.setFromPoints(axis, points);
    }
    const alignedSatBounds = this.alignedSatBounds;
    alignedSatBounds[0].setFromPointsField(points, "x");
    alignedSatBounds[1].setFromPointsField(points, "y");
    alignedSatBounds[2].setFromPointsField(points, "z");
    this.invMatrix.copy(this.matrix).invert();
    this.needsUpdate = false;
  };
}();
OrientedBox.prototype.intersectsBox = function() {
  const aabbBounds = new SeparatingAxisBounds();
  return function intersectsBox(box) {
    if (this.needsUpdate) {
      this.update();
    }
    const min = box.min;
    const max = box.max;
    const satBounds = this.satBounds;
    const satAxes = this.satAxes;
    const alignedSatBounds = this.alignedSatBounds;
    aabbBounds.min = min.x;
    aabbBounds.max = max.x;
    if (alignedSatBounds[0].isSeparated(aabbBounds))
      return false;
    aabbBounds.min = min.y;
    aabbBounds.max = max.y;
    if (alignedSatBounds[1].isSeparated(aabbBounds))
      return false;
    aabbBounds.min = min.z;
    aabbBounds.max = max.z;
    if (alignedSatBounds[2].isSeparated(aabbBounds))
      return false;
    for (let i = 0; i < 3; i++) {
      const axis = satAxes[i];
      const sb = satBounds[i];
      aabbBounds.setFromBox(axis, box);
      if (sb.isSeparated(aabbBounds))
        return false;
    }
    return true;
  };
}();
OrientedBox.prototype.intersectsTriangle = function() {
  const saTri = new ExtendedTriangle();
  const pointsArr = new Array(3);
  const cachedSatBounds = new SeparatingAxisBounds();
  const cachedSatBounds2 = new SeparatingAxisBounds();
  const cachedAxis = new Vector3();
  return function intersectsTriangle(triangle3) {
    if (this.needsUpdate) {
      this.update();
    }
    if (!triangle3.isExtendedTriangle) {
      saTri.copy(triangle3);
      saTri.update();
      triangle3 = saTri;
    } else if (triangle3.needsUpdate) {
      triangle3.update();
    }
    const satBounds = this.satBounds;
    const satAxes = this.satAxes;
    pointsArr[0] = triangle3.a;
    pointsArr[1] = triangle3.b;
    pointsArr[2] = triangle3.c;
    for (let i = 0; i < 3; i++) {
      const sb = satBounds[i];
      const sa = satAxes[i];
      cachedSatBounds.setFromPoints(sa, pointsArr);
      if (sb.isSeparated(cachedSatBounds))
        return false;
    }
    const triSatBounds = triangle3.satBounds;
    const triSatAxes = triangle3.satAxes;
    const points = this.points;
    for (let i = 0; i < 3; i++) {
      const sb = triSatBounds[i];
      const sa = triSatAxes[i];
      cachedSatBounds.setFromPoints(sa, points);
      if (sb.isSeparated(cachedSatBounds))
        return false;
    }
    for (let i = 0; i < 3; i++) {
      const sa1 = satAxes[i];
      for (let i2 = 0; i2 < 4; i2++) {
        const sa2 = triSatAxes[i2];
        cachedAxis.crossVectors(sa1, sa2);
        cachedSatBounds.setFromPoints(cachedAxis, pointsArr);
        cachedSatBounds2.setFromPoints(cachedAxis, points);
        if (cachedSatBounds.isSeparated(cachedSatBounds2))
          return false;
      }
    }
    return true;
  };
}();
OrientedBox.prototype.closestPointToPoint = /* @__PURE__ */ function() {
  return function closestPointToPoint2(point, target1) {
    if (this.needsUpdate) {
      this.update();
    }
    target1.copy(point).applyMatrix4(this.invMatrix).clamp(this.min, this.max).applyMatrix4(this.matrix);
    return target1;
  };
}();
OrientedBox.prototype.distanceToPoint = function() {
  const target = new Vector3();
  return function distanceToPoint(point) {
    this.closestPointToPoint(point, target);
    return point.distanceTo(target);
  };
}();
OrientedBox.prototype.distanceToBox = function() {
  const xyzFields = ["x", "y", "z"];
  const segments1 = new Array(12).fill().map(() => new Line3());
  const segments2 = new Array(12).fill().map(() => new Line3());
  const point1 = new Vector3();
  const point2 = new Vector3();
  return function distanceToBox(box, threshold = 0, target1 = null, target2 = null) {
    if (this.needsUpdate) {
      this.update();
    }
    if (this.intersectsBox(box)) {
      if (target1 || target2) {
        box.getCenter(point2);
        this.closestPointToPoint(point2, point1);
        box.closestPointToPoint(point1, point2);
        if (target1)
          target1.copy(point1);
        if (target2)
          target2.copy(point2);
      }
      return 0;
    }
    const threshold2 = threshold * threshold;
    const min = box.min;
    const max = box.max;
    const points = this.points;
    let closestDistanceSq = Infinity;
    for (let i = 0; i < 8; i++) {
      const p = points[i];
      point2.copy(p).clamp(min, max);
      const dist = p.distanceToSquared(point2);
      if (dist < closestDistanceSq) {
        closestDistanceSq = dist;
        if (target1)
          target1.copy(p);
        if (target2)
          target2.copy(point2);
        if (dist < threshold2)
          return Math.sqrt(dist);
      }
    }
    let count = 0;
    for (let i = 0; i < 3; i++) {
      for (let i1 = 0; i1 <= 1; i1++) {
        for (let i2 = 0; i2 <= 1; i2++) {
          const nextIndex = (i + 1) % 3;
          const nextIndex2 = (i + 2) % 3;
          const index = i1 << nextIndex | i2 << nextIndex2;
          const index2 = 1 << i | i1 << nextIndex | i2 << nextIndex2;
          const p1 = points[index];
          const p2 = points[index2];
          const line1 = segments1[count];
          line1.set(p1, p2);
          const f1 = xyzFields[i];
          const f2 = xyzFields[nextIndex];
          const f3 = xyzFields[nextIndex2];
          const line2 = segments2[count];
          const start = line2.start;
          const end = line2.end;
          start[f1] = min[f1];
          start[f2] = i1 ? min[f2] : max[f2];
          start[f3] = i2 ? min[f3] : max[f2];
          end[f1] = max[f1];
          end[f2] = i1 ? min[f2] : max[f2];
          end[f3] = i2 ? min[f3] : max[f2];
          count++;
        }
      }
    }
    for (let x = 0; x <= 1; x++) {
      for (let y = 0; y <= 1; y++) {
        for (let z = 0; z <= 1; z++) {
          point2.x = x ? max.x : min.x;
          point2.y = y ? max.y : min.y;
          point2.z = z ? max.z : min.z;
          this.closestPointToPoint(point2, point1);
          const dist = point2.distanceToSquared(point1);
          if (dist < closestDistanceSq) {
            closestDistanceSq = dist;
            if (target1)
              target1.copy(point1);
            if (target2)
              target2.copy(point2);
            if (dist < threshold2)
              return Math.sqrt(dist);
          }
        }
      }
    }
    for (let i = 0; i < 12; i++) {
      const l1 = segments1[i];
      for (let i2 = 0; i2 < 12; i2++) {
        const l2 = segments2[i2];
        closestPointsSegmentToSegment(l1, l2, point1, point2);
        const dist = point1.distanceToSquared(point2);
        if (dist < closestDistanceSq) {
          closestDistanceSq = dist;
          if (target1)
            target1.copy(point1);
          if (target2)
            target2.copy(point2);
          if (dist < threshold2)
            return Math.sqrt(dist);
        }
      }
    }
    return Math.sqrt(closestDistanceSq);
  };
}();
class PrimitivePool {
  constructor(getNewPrimitive) {
    this._getNewPrimitive = getNewPrimitive;
    this._primitives = [];
  }
  getPrimitive() {
    const primitives = this._primitives;
    if (primitives.length === 0) {
      return this._getNewPrimitive();
    } else {
      return primitives.pop();
    }
  }
  releasePrimitive(primitive) {
    this._primitives.push(primitive);
  }
}
class ExtendedTrianglePoolBase extends PrimitivePool {
  constructor() {
    super(() => new ExtendedTriangle());
  }
}
const ExtendedTrianglePool = /* @__PURE__ */ new ExtendedTrianglePoolBase();
function IS_LEAF(n16, uint16Array) {
  return uint16Array[n16 + 15] === 65535;
}
function OFFSET(n32, uint32Array) {
  return uint32Array[n32 + 6];
}
function COUNT(n16, uint16Array) {
  return uint16Array[n16 + 14];
}
function LEFT_NODE(n32) {
  return n32 + 8;
}
function RIGHT_NODE(n32, uint32Array) {
  return uint32Array[n32 + 6];
}
function SPLIT_AXIS(n32, uint32Array) {
  return uint32Array[n32 + 7];
}
function BOUNDING_DATA_INDEX(n32) {
  return n32;
}
class _BufferStack {
  constructor() {
    this.float32Array = null;
    this.uint16Array = null;
    this.uint32Array = null;
    const stack = [];
    let prevBuffer = null;
    this.setBuffer = (buffer) => {
      if (prevBuffer) {
        stack.push(prevBuffer);
      }
      prevBuffer = buffer;
      this.float32Array = new Float32Array(buffer);
      this.uint16Array = new Uint16Array(buffer);
      this.uint32Array = new Uint32Array(buffer);
    };
    this.clearBuffer = () => {
      prevBuffer = null;
      this.float32Array = null;
      this.uint16Array = null;
      this.uint32Array = null;
      if (stack.length !== 0) {
        this.setBuffer(stack.pop());
      }
    };
  }
}
const BufferStack = new _BufferStack();
let _box1, _box2;
const boxStack = [];
const boxPool = /* @__PURE__ */ new PrimitivePool(() => new Box3());
function shapecast(bvh, root, intersectsBounds, intersectsRange, boundsTraverseOrder, byteOffset) {
  _box1 = boxPool.getPrimitive();
  _box2 = boxPool.getPrimitive();
  boxStack.push(_box1, _box2);
  BufferStack.setBuffer(bvh._roots[root]);
  const result = shapecastTraverse(0, bvh.geometry, intersectsBounds, intersectsRange, boundsTraverseOrder, byteOffset);
  BufferStack.clearBuffer();
  boxPool.releasePrimitive(_box1);
  boxPool.releasePrimitive(_box2);
  boxStack.pop();
  boxStack.pop();
  const length = boxStack.length;
  if (length > 0) {
    _box2 = boxStack[length - 1];
    _box1 = boxStack[length - 2];
  }
  return result;
}
function shapecastTraverse(nodeIndex32, geometry, intersectsBoundsFunc, intersectsRangeFunc, nodeScoreFunc = null, nodeIndexByteOffset = 0, depth = 0) {
  const { float32Array, uint16Array, uint32Array } = BufferStack;
  let nodeIndex16 = nodeIndex32 * 2;
  const isLeaf = IS_LEAF(nodeIndex16, uint16Array);
  if (isLeaf) {
    const offset = OFFSET(nodeIndex32, uint32Array);
    const count = COUNT(nodeIndex16, uint16Array);
    arrayToBox(BOUNDING_DATA_INDEX(nodeIndex32), float32Array, _box1);
    return intersectsRangeFunc(offset, count, false, depth, nodeIndexByteOffset + nodeIndex32, _box1);
  } else {
    let getLeftOffset = function(nodeIndex322) {
      const { uint16Array: uint16Array2, uint32Array: uint32Array2 } = BufferStack;
      let nodeIndex162 = nodeIndex322 * 2;
      while (!IS_LEAF(nodeIndex162, uint16Array2)) {
        nodeIndex322 = LEFT_NODE(nodeIndex322);
        nodeIndex162 = nodeIndex322 * 2;
      }
      return OFFSET(nodeIndex322, uint32Array2);
    }, getRightEndOffset = function(nodeIndex322) {
      const { uint16Array: uint16Array2, uint32Array: uint32Array2 } = BufferStack;
      let nodeIndex162 = nodeIndex322 * 2;
      while (!IS_LEAF(nodeIndex162, uint16Array2)) {
        nodeIndex322 = RIGHT_NODE(nodeIndex322, uint32Array2);
        nodeIndex162 = nodeIndex322 * 2;
      }
      return OFFSET(nodeIndex322, uint32Array2) + COUNT(nodeIndex162, uint16Array2);
    };
    const left = LEFT_NODE(nodeIndex32);
    const right = RIGHT_NODE(nodeIndex32, uint32Array);
    let c1 = left;
    let c2 = right;
    let score1, score2;
    let box1, box2;
    if (nodeScoreFunc) {
      box1 = _box1;
      box2 = _box2;
      arrayToBox(BOUNDING_DATA_INDEX(c1), float32Array, box1);
      arrayToBox(BOUNDING_DATA_INDEX(c2), float32Array, box2);
      score1 = nodeScoreFunc(box1);
      score2 = nodeScoreFunc(box2);
      if (score2 < score1) {
        c1 = right;
        c2 = left;
        const temp5 = score1;
        score1 = score2;
        score2 = temp5;
        box1 = box2;
      }
    }
    if (!box1) {
      box1 = _box1;
      arrayToBox(BOUNDING_DATA_INDEX(c1), float32Array, box1);
    }
    const isC1Leaf = IS_LEAF(c1 * 2, uint16Array);
    const c1Intersection = intersectsBoundsFunc(box1, isC1Leaf, score1, depth + 1, nodeIndexByteOffset + c1);
    let c1StopTraversal;
    if (c1Intersection === CONTAINED) {
      const offset = getLeftOffset(c1);
      const end = getRightEndOffset(c1);
      const count = end - offset;
      c1StopTraversal = intersectsRangeFunc(offset, count, true, depth + 1, nodeIndexByteOffset + c1, box1);
    } else {
      c1StopTraversal = c1Intersection && shapecastTraverse(
        c1,
        geometry,
        intersectsBoundsFunc,
        intersectsRangeFunc,
        nodeScoreFunc,
        nodeIndexByteOffset,
        depth + 1
      );
    }
    if (c1StopTraversal)
      return true;
    box2 = _box2;
    arrayToBox(BOUNDING_DATA_INDEX(c2), float32Array, box2);
    const isC2Leaf = IS_LEAF(c2 * 2, uint16Array);
    const c2Intersection = intersectsBoundsFunc(box2, isC2Leaf, score2, depth + 1, nodeIndexByteOffset + c2);
    let c2StopTraversal;
    if (c2Intersection === CONTAINED) {
      const offset = getLeftOffset(c2);
      const end = getRightEndOffset(c2);
      const count = end - offset;
      c2StopTraversal = intersectsRangeFunc(offset, count, true, depth + 1, nodeIndexByteOffset + c2, box2);
    } else {
      c2StopTraversal = c2Intersection && shapecastTraverse(
        c2,
        geometry,
        intersectsBoundsFunc,
        intersectsRangeFunc,
        nodeScoreFunc,
        nodeIndexByteOffset,
        depth + 1
      );
    }
    if (c2StopTraversal)
      return true;
    return false;
  }
}
const temp = /* @__PURE__ */ new Vector3();
const temp1$2 = /* @__PURE__ */ new Vector3();
function closestPointToPoint(bvh, point, target = {}, minThreshold = 0, maxThreshold = Infinity) {
  const minThresholdSq = minThreshold * minThreshold;
  const maxThresholdSq = maxThreshold * maxThreshold;
  let closestDistanceSq = Infinity;
  let closestDistanceTriIndex = null;
  bvh.shapecast(
    {
      boundsTraverseOrder: (box) => {
        temp.copy(point).clamp(box.min, box.max);
        return temp.distanceToSquared(point);
      },
      intersectsBounds: (box, isLeaf, score) => {
        return score < closestDistanceSq && score < maxThresholdSq;
      },
      intersectsTriangle: (tri, triIndex) => {
        tri.closestPointToPoint(point, temp);
        const distSq = point.distanceToSquared(temp);
        if (distSq < closestDistanceSq) {
          temp1$2.copy(temp);
          closestDistanceSq = distSq;
          closestDistanceTriIndex = triIndex;
        }
        if (distSq < minThresholdSq) {
          return true;
        } else {
          return false;
        }
      }
    }
  );
  if (closestDistanceSq === Infinity)
    return null;
  const closestDistance = Math.sqrt(closestDistanceSq);
  if (!target.point)
    target.point = temp1$2.clone();
  else
    target.point.copy(temp1$2);
  target.distance = closestDistance, target.faceIndex = closestDistanceTriIndex;
  return target;
}
const _vA = /* @__PURE__ */ new Vector3();
const _vB = /* @__PURE__ */ new Vector3();
const _vC = /* @__PURE__ */ new Vector3();
const _uvA = /* @__PURE__ */ new Vector2();
const _uvB = /* @__PURE__ */ new Vector2();
const _uvC = /* @__PURE__ */ new Vector2();
const _normalA = /* @__PURE__ */ new Vector3();
const _normalB = /* @__PURE__ */ new Vector3();
const _normalC = /* @__PURE__ */ new Vector3();
const _intersectionPoint = /* @__PURE__ */ new Vector3();
function checkIntersection(ray2, pA, pB, pC, point, side) {
  let intersect;
  if (side === BackSide) {
    intersect = ray2.intersectTriangle(pC, pB, pA, true, point);
  } else {
    intersect = ray2.intersectTriangle(pA, pB, pC, side !== DoubleSide, point);
  }
  if (intersect === null)
    return null;
  const distance = ray2.origin.distanceTo(point);
  return {
    distance,
    point: point.clone()
  };
}
function checkBufferGeometryIntersection(ray2, position, normal, uv, uv1, a, b, c, side) {
  _vA.fromBufferAttribute(position, a);
  _vB.fromBufferAttribute(position, b);
  _vC.fromBufferAttribute(position, c);
  const intersection = checkIntersection(ray2, _vA, _vB, _vC, _intersectionPoint, side);
  if (intersection) {
    if (uv) {
      _uvA.fromBufferAttribute(uv, a);
      _uvB.fromBufferAttribute(uv, b);
      _uvC.fromBufferAttribute(uv, c);
      intersection.uv = Triangle.getInterpolation(_intersectionPoint, _vA, _vB, _vC, _uvA, _uvB, _uvC, new Vector2());
    }
    if (uv1) {
      _uvA.fromBufferAttribute(uv1, a);
      _uvB.fromBufferAttribute(uv1, b);
      _uvC.fromBufferAttribute(uv1, c);
      intersection.uv1 = Triangle.getInterpolation(_intersectionPoint, _vA, _vB, _vC, _uvA, _uvB, _uvC, new Vector2());
    }
    if (normal) {
      _normalA.fromBufferAttribute(normal, a);
      _normalB.fromBufferAttribute(normal, b);
      _normalC.fromBufferAttribute(normal, c);
      intersection.normal = Triangle.getInterpolation(_intersectionPoint, _vA, _vB, _vC, _normalA, _normalB, _normalC, new Vector3());
      if (intersection.normal.dot(ray2.direction) > 0) {
        intersection.normal.multiplyScalar(-1);
      }
    }
    const face = {
      a,
      b,
      c,
      normal: new Vector3(),
      materialIndex: 0
    };
    Triangle.getNormal(_vA, _vB, _vC, face.normal);
    intersection.face = face;
    intersection.faceIndex = a;
  }
  return intersection;
}
function intersectTri(geo, side, ray2, tri, intersections) {
  const triOffset = tri * 3;
  let a = triOffset + 0;
  let b = triOffset + 1;
  let c = triOffset + 2;
  const index = geo.index;
  if (geo.index) {
    a = index.getX(a);
    b = index.getX(b);
    c = index.getX(c);
  }
  const { position, normal, uv, uv1 } = geo.attributes;
  const intersection = checkBufferGeometryIntersection(ray2, position, normal, uv, uv1, a, b, c, side);
  if (intersection) {
    intersection.faceIndex = tri;
    if (intersections)
      intersections.push(intersection);
    return intersection;
  }
  return null;
}
function setTriangle(tri, i, index, pos) {
  const ta = tri.a;
  const tb = tri.b;
  const tc = tri.c;
  let i0 = i;
  let i1 = i + 1;
  let i2 = i + 2;
  if (index) {
    i0 = index.getX(i0);
    i1 = index.getX(i1);
    i2 = index.getX(i2);
  }
  ta.x = pos.getX(i0);
  ta.y = pos.getY(i0);
  ta.z = pos.getZ(i0);
  tb.x = pos.getX(i1);
  tb.y = pos.getY(i1);
  tb.z = pos.getZ(i1);
  tc.x = pos.getX(i2);
  tc.y = pos.getY(i2);
  tc.z = pos.getZ(i2);
}
function intersectTris(bvh, side, ray2, offset, count, intersections) {
  const { geometry, _indirectBuffer } = bvh;
  for (let i = offset, end = offset + count; i < end; i++) {
    intersectTri(geometry, side, ray2, i, intersections);
  }
}
function intersectClosestTri(bvh, side, ray2, offset, count) {
  const { geometry, _indirectBuffer } = bvh;
  let dist = Infinity;
  let res = null;
  for (let i = offset, end = offset + count; i < end; i++) {
    let intersection;
    intersection = intersectTri(geometry, side, ray2, i);
    if (intersection && intersection.distance < dist) {
      res = intersection;
      dist = intersection.distance;
    }
  }
  return res;
}
function iterateOverTriangles(offset, count, bvh, intersectsTriangleFunc, contained, depth, triangle3) {
  const { geometry } = bvh;
  const { index } = geometry;
  const pos = geometry.attributes.position;
  for (let i = offset, l = count + offset; i < l; i++) {
    let tri;
    tri = i;
    setTriangle(triangle3, tri * 3, index, pos);
    triangle3.needsUpdate = true;
    if (intersectsTriangleFunc(triangle3, tri, contained, depth)) {
      return true;
    }
  }
  return false;
}
function refit(bvh, nodeIndices = null) {
  if (nodeIndices && Array.isArray(nodeIndices)) {
    nodeIndices = new Set(nodeIndices);
  }
  const geometry = bvh.geometry;
  const indexArr = geometry.index ? geometry.index.array : null;
  const posAttr = geometry.attributes.position;
  let buffer, uint32Array, uint16Array, float32Array;
  let byteOffset = 0;
  const roots = bvh._roots;
  for (let i = 0, l = roots.length; i < l; i++) {
    buffer = roots[i];
    uint32Array = new Uint32Array(buffer);
    uint16Array = new Uint16Array(buffer);
    float32Array = new Float32Array(buffer);
    _traverse2(0, byteOffset);
    byteOffset += buffer.byteLength;
  }
  function _traverse2(node32Index, byteOffset2, force = false) {
    const node16Index = node32Index * 2;
    const isLeaf = uint16Array[node16Index + 15] === IS_LEAFNODE_FLAG;
    if (isLeaf) {
      const offset = uint32Array[node32Index + 6];
      const count = uint16Array[node16Index + 14];
      let minx = Infinity;
      let miny = Infinity;
      let minz = Infinity;
      let maxx = -Infinity;
      let maxy = -Infinity;
      let maxz = -Infinity;
      for (let i = 3 * offset, l = 3 * (offset + count); i < l; i++) {
        let index = indexArr[i];
        const x = posAttr.getX(index);
        const y = posAttr.getY(index);
        const z = posAttr.getZ(index);
        if (x < minx)
          minx = x;
        if (x > maxx)
          maxx = x;
        if (y < miny)
          miny = y;
        if (y > maxy)
          maxy = y;
        if (z < minz)
          minz = z;
        if (z > maxz)
          maxz = z;
      }
      if (float32Array[node32Index + 0] !== minx || float32Array[node32Index + 1] !== miny || float32Array[node32Index + 2] !== minz || float32Array[node32Index + 3] !== maxx || float32Array[node32Index + 4] !== maxy || float32Array[node32Index + 5] !== maxz) {
        float32Array[node32Index + 0] = minx;
        float32Array[node32Index + 1] = miny;
        float32Array[node32Index + 2] = minz;
        float32Array[node32Index + 3] = maxx;
        float32Array[node32Index + 4] = maxy;
        float32Array[node32Index + 5] = maxz;
        return true;
      } else {
        return false;
      }
    } else {
      const left = node32Index + 8;
      const right = uint32Array[node32Index + 6];
      const offsetLeft = left + byteOffset2;
      const offsetRight = right + byteOffset2;
      let forceChildren = force;
      let includesLeft = false;
      let includesRight = false;
      if (nodeIndices) {
        if (!forceChildren) {
          includesLeft = nodeIndices.has(offsetLeft);
          includesRight = nodeIndices.has(offsetRight);
          forceChildren = !includesLeft && !includesRight;
        }
      } else {
        includesLeft = true;
        includesRight = true;
      }
      const traverseLeft = forceChildren || includesLeft;
      const traverseRight = forceChildren || includesRight;
      let leftChange = false;
      if (traverseLeft) {
        leftChange = _traverse2(left, byteOffset2, forceChildren);
      }
      let rightChange = false;
      if (traverseRight) {
        rightChange = _traverse2(right, byteOffset2, forceChildren);
      }
      const didChange = leftChange || rightChange;
      if (didChange) {
        for (let i = 0; i < 3; i++) {
          const lefti = left + i;
          const righti = right + i;
          const minLeftValue = float32Array[lefti];
          const maxLeftValue = float32Array[lefti + 3];
          const minRightValue = float32Array[righti];
          const maxRightValue = float32Array[righti + 3];
          float32Array[node32Index + i] = minLeftValue < minRightValue ? minLeftValue : minRightValue;
          float32Array[node32Index + i + 3] = maxLeftValue > maxRightValue ? maxLeftValue : maxRightValue;
        }
      }
      return didChange;
    }
  }
}
const _boundingBox = /* @__PURE__ */ new Box3();
function intersectRay(nodeIndex32, array, ray2, target) {
  arrayToBox(nodeIndex32, array, _boundingBox);
  return ray2.intersectBox(_boundingBox, target);
}
function intersectTris_indirect(bvh, side, ray2, offset, count, intersections) {
  const { geometry, _indirectBuffer } = bvh;
  for (let i = offset, end = offset + count; i < end; i++) {
    let vi = _indirectBuffer ? _indirectBuffer[i] : i;
    intersectTri(geometry, side, ray2, vi, intersections);
  }
}
function intersectClosestTri_indirect(bvh, side, ray2, offset, count) {
  const { geometry, _indirectBuffer } = bvh;
  let dist = Infinity;
  let res = null;
  for (let i = offset, end = offset + count; i < end; i++) {
    let intersection;
    intersection = intersectTri(geometry, side, ray2, _indirectBuffer ? _indirectBuffer[i] : i);
    if (intersection && intersection.distance < dist) {
      res = intersection;
      dist = intersection.distance;
    }
  }
  return res;
}
function iterateOverTriangles_indirect(offset, count, bvh, intersectsTriangleFunc, contained, depth, triangle3) {
  const { geometry } = bvh;
  const { index } = geometry;
  const pos = geometry.attributes.position;
  for (let i = offset, l = count + offset; i < l; i++) {
    let tri;
    tri = bvh.resolveTriangleIndex(i);
    setTriangle(triangle3, tri * 3, index, pos);
    triangle3.needsUpdate = true;
    if (intersectsTriangleFunc(triangle3, tri, contained, depth)) {
      return true;
    }
  }
  return false;
}
const _boxIntersection$3 = /* @__PURE__ */ new Vector3();
function raycast(bvh, root, side, ray2, intersects) {
  BufferStack.setBuffer(bvh._roots[root]);
  _raycast$1(0, bvh, side, ray2, intersects);
  BufferStack.clearBuffer();
}
function _raycast$1(nodeIndex32, bvh, side, ray2, intersects) {
  const { float32Array, uint16Array, uint32Array } = BufferStack;
  const nodeIndex16 = nodeIndex32 * 2;
  const isLeaf = IS_LEAF(nodeIndex16, uint16Array);
  if (isLeaf) {
    const offset = OFFSET(nodeIndex32, uint32Array);
    const count = COUNT(nodeIndex16, uint16Array);
    intersectTris(bvh, side, ray2, offset, count, intersects);
  } else {
    const leftIndex = LEFT_NODE(nodeIndex32);
    if (intersectRay(leftIndex, float32Array, ray2, _boxIntersection$3)) {
      _raycast$1(leftIndex, bvh, side, ray2, intersects);
    }
    const rightIndex = RIGHT_NODE(nodeIndex32, uint32Array);
    if (intersectRay(rightIndex, float32Array, ray2, _boxIntersection$3)) {
      _raycast$1(rightIndex, bvh, side, ray2, intersects);
    }
  }
}
const _boxIntersection$2 = /* @__PURE__ */ new Vector3();
const _xyzFields$1 = ["x", "y", "z"];
function raycastFirst(bvh, root, side, ray2) {
  BufferStack.setBuffer(bvh._roots[root]);
  const result = _raycastFirst$1(0, bvh, side, ray2);
  BufferStack.clearBuffer();
  return result;
}
function _raycastFirst$1(nodeIndex32, bvh, side, ray2) {
  const { float32Array, uint16Array, uint32Array } = BufferStack;
  let nodeIndex16 = nodeIndex32 * 2;
  const isLeaf = IS_LEAF(nodeIndex16, uint16Array);
  if (isLeaf) {
    const offset = OFFSET(nodeIndex32, uint32Array);
    const count = COUNT(nodeIndex16, uint16Array);
    return intersectClosestTri(bvh, side, ray2, offset, count);
  } else {
    const splitAxis = SPLIT_AXIS(nodeIndex32, uint32Array);
    const xyzAxis = _xyzFields$1[splitAxis];
    const rayDir = ray2.direction[xyzAxis];
    const leftToRight = rayDir >= 0;
    let c1, c2;
    if (leftToRight) {
      c1 = LEFT_NODE(nodeIndex32);
      c2 = RIGHT_NODE(nodeIndex32, uint32Array);
    } else {
      c1 = RIGHT_NODE(nodeIndex32, uint32Array);
      c2 = LEFT_NODE(nodeIndex32);
    }
    const c1Intersection = intersectRay(c1, float32Array, ray2, _boxIntersection$2);
    const c1Result = c1Intersection ? _raycastFirst$1(c1, bvh, side, ray2) : null;
    if (c1Result) {
      const point = c1Result.point[xyzAxis];
      const isOutside = leftToRight ? point <= float32Array[c2 + splitAxis] : (
        // min bounding data
        point >= float32Array[c2 + splitAxis + 3]
      );
      if (isOutside) {
        return c1Result;
      }
    }
    const c2Intersection = intersectRay(c2, float32Array, ray2, _boxIntersection$2);
    const c2Result = c2Intersection ? _raycastFirst$1(c2, bvh, side, ray2) : null;
    if (c1Result && c2Result) {
      return c1Result.distance <= c2Result.distance ? c1Result : c2Result;
    } else {
      return c1Result || c2Result || null;
    }
  }
}
const boundingBox$1 = /* @__PURE__ */ new Box3();
const triangle$1 = /* @__PURE__ */ new ExtendedTriangle();
const triangle2$1 = /* @__PURE__ */ new ExtendedTriangle();
const invertedMat$1 = /* @__PURE__ */ new Matrix4();
const obb$4 = /* @__PURE__ */ new OrientedBox();
const obb2$3 = /* @__PURE__ */ new OrientedBox();
function intersectsGeometry(bvh, root, otherGeometry, geometryToBvh) {
  BufferStack.setBuffer(bvh._roots[root]);
  const result = _intersectsGeometry$1(0, bvh, otherGeometry, geometryToBvh);
  BufferStack.clearBuffer();
  return result;
}
function _intersectsGeometry$1(nodeIndex32, bvh, otherGeometry, geometryToBvh, cachedObb = null) {
  const { float32Array, uint16Array, uint32Array } = BufferStack;
  let nodeIndex16 = nodeIndex32 * 2;
  if (cachedObb === null) {
    if (!otherGeometry.boundingBox) {
      otherGeometry.computeBoundingBox();
    }
    obb$4.set(otherGeometry.boundingBox.min, otherGeometry.boundingBox.max, geometryToBvh);
    cachedObb = obb$4;
  }
  const isLeaf = IS_LEAF(nodeIndex16, uint16Array);
  if (isLeaf) {
    const thisGeometry = bvh.geometry;
    const thisIndex = thisGeometry.index;
    const thisPos = thisGeometry.attributes.position;
    const index = otherGeometry.index;
    const pos = otherGeometry.attributes.position;
    const offset = OFFSET(nodeIndex32, uint32Array);
    const count = COUNT(nodeIndex16, uint16Array);
    invertedMat$1.copy(geometryToBvh).invert();
    if (otherGeometry.boundsTree) {
      arrayToBox(BOUNDING_DATA_INDEX(nodeIndex32), float32Array, obb2$3);
      obb2$3.matrix.copy(invertedMat$1);
      obb2$3.needsUpdate = true;
      const res = otherGeometry.boundsTree.shapecast({
        intersectsBounds: (box) => obb2$3.intersectsBox(box),
        intersectsTriangle: (tri) => {
          tri.a.applyMatrix4(geometryToBvh);
          tri.b.applyMatrix4(geometryToBvh);
          tri.c.applyMatrix4(geometryToBvh);
          tri.needsUpdate = true;
          for (let i = offset * 3, l = (count + offset) * 3; i < l; i += 3) {
            setTriangle(triangle2$1, i, thisIndex, thisPos);
            triangle2$1.needsUpdate = true;
            if (tri.intersectsTriangle(triangle2$1)) {
              return true;
            }
          }
          return false;
        }
      });
      return res;
    } else {
      for (let i = offset * 3, l = (count + offset) * 3; i < l; i += 3) {
        setTriangle(triangle$1, i, thisIndex, thisPos);
        triangle$1.a.applyMatrix4(invertedMat$1);
        triangle$1.b.applyMatrix4(invertedMat$1);
        triangle$1.c.applyMatrix4(invertedMat$1);
        triangle$1.needsUpdate = true;
        for (let i2 = 0, l2 = index.count; i2 < l2; i2 += 3) {
          setTriangle(triangle2$1, i2, index, pos);
          triangle2$1.needsUpdate = true;
          if (triangle$1.intersectsTriangle(triangle2$1)) {
            return true;
          }
        }
      }
    }
  } else {
    const left = nodeIndex32 + 8;
    const right = uint32Array[nodeIndex32 + 6];
    arrayToBox(BOUNDING_DATA_INDEX(left), float32Array, boundingBox$1);
    const leftIntersection = cachedObb.intersectsBox(boundingBox$1) && _intersectsGeometry$1(left, bvh, otherGeometry, geometryToBvh, cachedObb);
    if (leftIntersection)
      return true;
    arrayToBox(BOUNDING_DATA_INDEX(right), float32Array, boundingBox$1);
    const rightIntersection = cachedObb.intersectsBox(boundingBox$1) && _intersectsGeometry$1(right, bvh, otherGeometry, geometryToBvh, cachedObb);
    if (rightIntersection)
      return true;
    return false;
  }
}
const tempMatrix$1 = /* @__PURE__ */ new Matrix4();
const obb$3 = /* @__PURE__ */ new OrientedBox();
const obb2$2 = /* @__PURE__ */ new OrientedBox();
const temp1$1 = /* @__PURE__ */ new Vector3();
const temp2$1 = /* @__PURE__ */ new Vector3();
const temp3$1 = /* @__PURE__ */ new Vector3();
const temp4$1 = /* @__PURE__ */ new Vector3();
function closestPointToGeometry(bvh, otherGeometry, geometryToBvh, target1 = {}, target2 = {}, minThreshold = 0, maxThreshold = Infinity) {
  if (!otherGeometry.boundingBox) {
    otherGeometry.computeBoundingBox();
  }
  obb$3.set(otherGeometry.boundingBox.min, otherGeometry.boundingBox.max, geometryToBvh);
  obb$3.needsUpdate = true;
  const geometry = bvh.geometry;
  const pos = geometry.attributes.position;
  const index = geometry.index;
  const otherPos = otherGeometry.attributes.position;
  const otherIndex = otherGeometry.index;
  const triangle3 = ExtendedTrianglePool.getPrimitive();
  const triangle22 = ExtendedTrianglePool.getPrimitive();
  let tempTarget1 = temp1$1;
  let tempTargetDest1 = temp2$1;
  let tempTarget2 = null;
  let tempTargetDest2 = null;
  if (target2) {
    tempTarget2 = temp3$1;
    tempTargetDest2 = temp4$1;
  }
  let closestDistance = Infinity;
  let closestDistanceTriIndex = null;
  let closestDistanceOtherTriIndex = null;
  tempMatrix$1.copy(geometryToBvh).invert();
  obb2$2.matrix.copy(tempMatrix$1);
  bvh.shapecast(
    {
      boundsTraverseOrder: (box) => {
        return obb$3.distanceToBox(box);
      },
      intersectsBounds: (box, isLeaf, score) => {
        if (score < closestDistance && score < maxThreshold) {
          if (isLeaf) {
            obb2$2.min.copy(box.min);
            obb2$2.max.copy(box.max);
            obb2$2.needsUpdate = true;
          }
          return true;
        }
        return false;
      },
      intersectsRange: (offset, count) => {
        if (otherGeometry.boundsTree) {
          const otherBvh = otherGeometry.boundsTree;
          return otherBvh.shapecast({
            boundsTraverseOrder: (box) => {
              return obb2$2.distanceToBox(box);
            },
            intersectsBounds: (box, isLeaf, score) => {
              return score < closestDistance && score < maxThreshold;
            },
            intersectsRange: (otherOffset, otherCount) => {
              for (let i2 = otherOffset, l2 = otherOffset + otherCount; i2 < l2; i2++) {
                setTriangle(triangle22, 3 * i2, otherIndex, otherPos);
                triangle22.a.applyMatrix4(geometryToBvh);
                triangle22.b.applyMatrix4(geometryToBvh);
                triangle22.c.applyMatrix4(geometryToBvh);
                triangle22.needsUpdate = true;
                for (let i = offset, l = offset + count; i < l; i++) {
                  setTriangle(triangle3, 3 * i, index, pos);
                  triangle3.needsUpdate = true;
                  const dist = triangle3.distanceToTriangle(triangle22, tempTarget1, tempTarget2);
                  if (dist < closestDistance) {
                    tempTargetDest1.copy(tempTarget1);
                    if (tempTargetDest2) {
                      tempTargetDest2.copy(tempTarget2);
                    }
                    closestDistance = dist;
                    closestDistanceTriIndex = i;
                    closestDistanceOtherTriIndex = i2;
                  }
                  if (dist < minThreshold) {
                    return true;
                  }
                }
              }
            }
          });
        } else {
          const triCount = getTriCount(otherGeometry);
          for (let i2 = 0, l2 = triCount; i2 < l2; i2++) {
            setTriangle(triangle22, 3 * i2, otherIndex, otherPos);
            triangle22.a.applyMatrix4(geometryToBvh);
            triangle22.b.applyMatrix4(geometryToBvh);
            triangle22.c.applyMatrix4(geometryToBvh);
            triangle22.needsUpdate = true;
            for (let i = offset, l = offset + count; i < l; i++) {
              setTriangle(triangle3, 3 * i, index, pos);
              triangle3.needsUpdate = true;
              const dist = triangle3.distanceToTriangle(triangle22, tempTarget1, tempTarget2);
              if (dist < closestDistance) {
                tempTargetDest1.copy(tempTarget1);
                if (tempTargetDest2) {
                  tempTargetDest2.copy(tempTarget2);
                }
                closestDistance = dist;
                closestDistanceTriIndex = i;
                closestDistanceOtherTriIndex = i2;
              }
              if (dist < minThreshold) {
                return true;
              }
            }
          }
        }
      }
    }
  );
  ExtendedTrianglePool.releasePrimitive(triangle3);
  ExtendedTrianglePool.releasePrimitive(triangle22);
  if (closestDistance === Infinity) {
    return null;
  }
  if (!target1.point) {
    target1.point = tempTargetDest1.clone();
  } else {
    target1.point.copy(tempTargetDest1);
  }
  target1.distance = closestDistance, target1.faceIndex = closestDistanceTriIndex;
  if (target2) {
    if (!target2.point)
      target2.point = tempTargetDest2.clone();
    else
      target2.point.copy(tempTargetDest2);
    target2.point.applyMatrix4(tempMatrix$1);
    tempTargetDest1.applyMatrix4(tempMatrix$1);
    target2.distance = tempTargetDest1.sub(target2.point).length();
    target2.faceIndex = closestDistanceOtherTriIndex;
  }
  return target1;
}
function refit_indirect(bvh, nodeIndices = null) {
  if (nodeIndices && Array.isArray(nodeIndices)) {
    nodeIndices = new Set(nodeIndices);
  }
  const geometry = bvh.geometry;
  const indexArr = geometry.index ? geometry.index.array : null;
  const posAttr = geometry.attributes.position;
  let buffer, uint32Array, uint16Array, float32Array;
  let byteOffset = 0;
  const roots = bvh._roots;
  for (let i = 0, l = roots.length; i < l; i++) {
    buffer = roots[i];
    uint32Array = new Uint32Array(buffer);
    uint16Array = new Uint16Array(buffer);
    float32Array = new Float32Array(buffer);
    _traverse2(0, byteOffset);
    byteOffset += buffer.byteLength;
  }
  function _traverse2(node32Index, byteOffset2, force = false) {
    const node16Index = node32Index * 2;
    const isLeaf = uint16Array[node16Index + 15] === IS_LEAFNODE_FLAG;
    if (isLeaf) {
      const offset = uint32Array[node32Index + 6];
      const count = uint16Array[node16Index + 14];
      let minx = Infinity;
      let miny = Infinity;
      let minz = Infinity;
      let maxx = -Infinity;
      let maxy = -Infinity;
      let maxz = -Infinity;
      for (let i = offset, l = offset + count; i < l; i++) {
        const t = 3 * bvh.resolveTriangleIndex(i);
        for (let j = 0; j < 3; j++) {
          let index = t + j;
          index = indexArr ? indexArr[index] : index;
          const x = posAttr.getX(index);
          const y = posAttr.getY(index);
          const z = posAttr.getZ(index);
          if (x < minx)
            minx = x;
          if (x > maxx)
            maxx = x;
          if (y < miny)
            miny = y;
          if (y > maxy)
            maxy = y;
          if (z < minz)
            minz = z;
          if (z > maxz)
            maxz = z;
        }
      }
      if (float32Array[node32Index + 0] !== minx || float32Array[node32Index + 1] !== miny || float32Array[node32Index + 2] !== minz || float32Array[node32Index + 3] !== maxx || float32Array[node32Index + 4] !== maxy || float32Array[node32Index + 5] !== maxz) {
        float32Array[node32Index + 0] = minx;
        float32Array[node32Index + 1] = miny;
        float32Array[node32Index + 2] = minz;
        float32Array[node32Index + 3] = maxx;
        float32Array[node32Index + 4] = maxy;
        float32Array[node32Index + 5] = maxz;
        return true;
      } else {
        return false;
      }
    } else {
      const left = node32Index + 8;
      const right = uint32Array[node32Index + 6];
      const offsetLeft = left + byteOffset2;
      const offsetRight = right + byteOffset2;
      let forceChildren = force;
      let includesLeft = false;
      let includesRight = false;
      if (nodeIndices) {
        if (!forceChildren) {
          includesLeft = nodeIndices.has(offsetLeft);
          includesRight = nodeIndices.has(offsetRight);
          forceChildren = !includesLeft && !includesRight;
        }
      } else {
        includesLeft = true;
        includesRight = true;
      }
      const traverseLeft = forceChildren || includesLeft;
      const traverseRight = forceChildren || includesRight;
      let leftChange = false;
      if (traverseLeft) {
        leftChange = _traverse2(left, byteOffset2, forceChildren);
      }
      let rightChange = false;
      if (traverseRight) {
        rightChange = _traverse2(right, byteOffset2, forceChildren);
      }
      const didChange = leftChange || rightChange;
      if (didChange) {
        for (let i = 0; i < 3; i++) {
          const lefti = left + i;
          const righti = right + i;
          const minLeftValue = float32Array[lefti];
          const maxLeftValue = float32Array[lefti + 3];
          const minRightValue = float32Array[righti];
          const maxRightValue = float32Array[righti + 3];
          float32Array[node32Index + i] = minLeftValue < minRightValue ? minLeftValue : minRightValue;
          float32Array[node32Index + i + 3] = maxLeftValue > maxRightValue ? maxLeftValue : maxRightValue;
        }
      }
      return didChange;
    }
  }
}
const _boxIntersection$1 = /* @__PURE__ */ new Vector3();
function raycast_indirect(bvh, root, side, ray2, intersects) {
  BufferStack.setBuffer(bvh._roots[root]);
  _raycast(0, bvh, side, ray2, intersects);
  BufferStack.clearBuffer();
}
function _raycast(nodeIndex32, bvh, side, ray2, intersects) {
  const { float32Array, uint16Array, uint32Array } = BufferStack;
  const nodeIndex16 = nodeIndex32 * 2;
  const isLeaf = IS_LEAF(nodeIndex16, uint16Array);
  if (isLeaf) {
    const offset = OFFSET(nodeIndex32, uint32Array);
    const count = COUNT(nodeIndex16, uint16Array);
    intersectTris_indirect(bvh, side, ray2, offset, count, intersects);
  } else {
    const leftIndex = LEFT_NODE(nodeIndex32);
    if (intersectRay(leftIndex, float32Array, ray2, _boxIntersection$1)) {
      _raycast(leftIndex, bvh, side, ray2, intersects);
    }
    const rightIndex = RIGHT_NODE(nodeIndex32, uint32Array);
    if (intersectRay(rightIndex, float32Array, ray2, _boxIntersection$1)) {
      _raycast(rightIndex, bvh, side, ray2, intersects);
    }
  }
}
const _boxIntersection = /* @__PURE__ */ new Vector3();
const _xyzFields = ["x", "y", "z"];
function raycastFirst_indirect(bvh, root, side, ray2) {
  BufferStack.setBuffer(bvh._roots[root]);
  const result = _raycastFirst(0, bvh, side, ray2);
  BufferStack.clearBuffer();
  return result;
}
function _raycastFirst(nodeIndex32, bvh, side, ray2) {
  const { float32Array, uint16Array, uint32Array } = BufferStack;
  let nodeIndex16 = nodeIndex32 * 2;
  const isLeaf = IS_LEAF(nodeIndex16, uint16Array);
  if (isLeaf) {
    const offset = OFFSET(nodeIndex32, uint32Array);
    const count = COUNT(nodeIndex16, uint16Array);
    return intersectClosestTri_indirect(bvh, side, ray2, offset, count);
  } else {
    const splitAxis = SPLIT_AXIS(nodeIndex32, uint32Array);
    const xyzAxis = _xyzFields[splitAxis];
    const rayDir = ray2.direction[xyzAxis];
    const leftToRight = rayDir >= 0;
    let c1, c2;
    if (leftToRight) {
      c1 = LEFT_NODE(nodeIndex32);
      c2 = RIGHT_NODE(nodeIndex32, uint32Array);
    } else {
      c1 = RIGHT_NODE(nodeIndex32, uint32Array);
      c2 = LEFT_NODE(nodeIndex32);
    }
    const c1Intersection = intersectRay(c1, float32Array, ray2, _boxIntersection);
    const c1Result = c1Intersection ? _raycastFirst(c1, bvh, side, ray2) : null;
    if (c1Result) {
      const point = c1Result.point[xyzAxis];
      const isOutside = leftToRight ? point <= float32Array[c2 + splitAxis] : (
        // min bounding data
        point >= float32Array[c2 + splitAxis + 3]
      );
      if (isOutside) {
        return c1Result;
      }
    }
    const c2Intersection = intersectRay(c2, float32Array, ray2, _boxIntersection);
    const c2Result = c2Intersection ? _raycastFirst(c2, bvh, side, ray2) : null;
    if (c1Result && c2Result) {
      return c1Result.distance <= c2Result.distance ? c1Result : c2Result;
    } else {
      return c1Result || c2Result || null;
    }
  }
}
const boundingBox = /* @__PURE__ */ new Box3();
const triangle = /* @__PURE__ */ new ExtendedTriangle();
const triangle2 = /* @__PURE__ */ new ExtendedTriangle();
const invertedMat = /* @__PURE__ */ new Matrix4();
const obb$2 = /* @__PURE__ */ new OrientedBox();
const obb2$1 = /* @__PURE__ */ new OrientedBox();
function intersectsGeometry_indirect(bvh, root, otherGeometry, geometryToBvh) {
  BufferStack.setBuffer(bvh._roots[root]);
  const result = _intersectsGeometry(0, bvh, otherGeometry, geometryToBvh);
  BufferStack.clearBuffer();
  return result;
}
function _intersectsGeometry(nodeIndex32, bvh, otherGeometry, geometryToBvh, cachedObb = null) {
  const { float32Array, uint16Array, uint32Array } = BufferStack;
  let nodeIndex16 = nodeIndex32 * 2;
  if (cachedObb === null) {
    if (!otherGeometry.boundingBox) {
      otherGeometry.computeBoundingBox();
    }
    obb$2.set(otherGeometry.boundingBox.min, otherGeometry.boundingBox.max, geometryToBvh);
    cachedObb = obb$2;
  }
  const isLeaf = IS_LEAF(nodeIndex16, uint16Array);
  if (isLeaf) {
    const thisGeometry = bvh.geometry;
    const thisIndex = thisGeometry.index;
    const thisPos = thisGeometry.attributes.position;
    const index = otherGeometry.index;
    const pos = otherGeometry.attributes.position;
    const offset = OFFSET(nodeIndex32, uint32Array);
    const count = COUNT(nodeIndex16, uint16Array);
    invertedMat.copy(geometryToBvh).invert();
    if (otherGeometry.boundsTree) {
      arrayToBox(BOUNDING_DATA_INDEX(nodeIndex32), float32Array, obb2$1);
      obb2$1.matrix.copy(invertedMat);
      obb2$1.needsUpdate = true;
      const res = otherGeometry.boundsTree.shapecast({
        intersectsBounds: (box) => obb2$1.intersectsBox(box),
        intersectsTriangle: (tri) => {
          tri.a.applyMatrix4(geometryToBvh);
          tri.b.applyMatrix4(geometryToBvh);
          tri.c.applyMatrix4(geometryToBvh);
          tri.needsUpdate = true;
          for (let i = offset, l = count + offset; i < l; i++) {
            setTriangle(triangle2, 3 * bvh.resolveTriangleIndex(i), thisIndex, thisPos);
            triangle2.needsUpdate = true;
            if (tri.intersectsTriangle(triangle2)) {
              return true;
            }
          }
          return false;
        }
      });
      return res;
    } else {
      for (let i = offset, l = count + offset; i < l; i++) {
        const ti = bvh.resolveTriangleIndex(i);
        setTriangle(triangle, 3 * ti, thisIndex, thisPos);
        triangle.a.applyMatrix4(invertedMat);
        triangle.b.applyMatrix4(invertedMat);
        triangle.c.applyMatrix4(invertedMat);
        triangle.needsUpdate = true;
        for (let i2 = 0, l2 = index.count; i2 < l2; i2 += 3) {
          setTriangle(triangle2, i2, index, pos);
          triangle2.needsUpdate = true;
          if (triangle.intersectsTriangle(triangle2)) {
            return true;
          }
        }
      }
    }
  } else {
    const left = nodeIndex32 + 8;
    const right = uint32Array[nodeIndex32 + 6];
    arrayToBox(BOUNDING_DATA_INDEX(left), float32Array, boundingBox);
    const leftIntersection = cachedObb.intersectsBox(boundingBox) && _intersectsGeometry(left, bvh, otherGeometry, geometryToBvh, cachedObb);
    if (leftIntersection)
      return true;
    arrayToBox(BOUNDING_DATA_INDEX(right), float32Array, boundingBox);
    const rightIntersection = cachedObb.intersectsBox(boundingBox) && _intersectsGeometry(right, bvh, otherGeometry, geometryToBvh, cachedObb);
    if (rightIntersection)
      return true;
    return false;
  }
}
const tempMatrix = /* @__PURE__ */ new Matrix4();
const obb$1 = /* @__PURE__ */ new OrientedBox();
const obb2 = /* @__PURE__ */ new OrientedBox();
const temp1 = /* @__PURE__ */ new Vector3();
const temp2 = /* @__PURE__ */ new Vector3();
const temp3 = /* @__PURE__ */ new Vector3();
const temp4 = /* @__PURE__ */ new Vector3();
function closestPointToGeometry_indirect(bvh, otherGeometry, geometryToBvh, target1 = {}, target2 = {}, minThreshold = 0, maxThreshold = Infinity) {
  if (!otherGeometry.boundingBox) {
    otherGeometry.computeBoundingBox();
  }
  obb$1.set(otherGeometry.boundingBox.min, otherGeometry.boundingBox.max, geometryToBvh);
  obb$1.needsUpdate = true;
  const geometry = bvh.geometry;
  const pos = geometry.attributes.position;
  const index = geometry.index;
  const otherPos = otherGeometry.attributes.position;
  const otherIndex = otherGeometry.index;
  const triangle3 = ExtendedTrianglePool.getPrimitive();
  const triangle22 = ExtendedTrianglePool.getPrimitive();
  let tempTarget1 = temp1;
  let tempTargetDest1 = temp2;
  let tempTarget2 = null;
  let tempTargetDest2 = null;
  if (target2) {
    tempTarget2 = temp3;
    tempTargetDest2 = temp4;
  }
  let closestDistance = Infinity;
  let closestDistanceTriIndex = null;
  let closestDistanceOtherTriIndex = null;
  tempMatrix.copy(geometryToBvh).invert();
  obb2.matrix.copy(tempMatrix);
  bvh.shapecast(
    {
      boundsTraverseOrder: (box) => {
        return obb$1.distanceToBox(box);
      },
      intersectsBounds: (box, isLeaf, score) => {
        if (score < closestDistance && score < maxThreshold) {
          if (isLeaf) {
            obb2.min.copy(box.min);
            obb2.max.copy(box.max);
            obb2.needsUpdate = true;
          }
          return true;
        }
        return false;
      },
      intersectsRange: (offset, count) => {
        if (otherGeometry.boundsTree) {
          const otherBvh = otherGeometry.boundsTree;
          return otherBvh.shapecast({
            boundsTraverseOrder: (box) => {
              return obb2.distanceToBox(box);
            },
            intersectsBounds: (box, isLeaf, score) => {
              return score < closestDistance && score < maxThreshold;
            },
            intersectsRange: (otherOffset, otherCount) => {
              for (let i2 = otherOffset, l2 = otherOffset + otherCount; i2 < l2; i2++) {
                const ti2 = otherBvh.resolveTriangleIndex(i2);
                setTriangle(triangle22, 3 * ti2, otherIndex, otherPos);
                triangle22.a.applyMatrix4(geometryToBvh);
                triangle22.b.applyMatrix4(geometryToBvh);
                triangle22.c.applyMatrix4(geometryToBvh);
                triangle22.needsUpdate = true;
                for (let i = offset, l = offset + count; i < l; i++) {
                  const ti = bvh.resolveTriangleIndex(i);
                  setTriangle(triangle3, 3 * ti, index, pos);
                  triangle3.needsUpdate = true;
                  const dist = triangle3.distanceToTriangle(triangle22, tempTarget1, tempTarget2);
                  if (dist < closestDistance) {
                    tempTargetDest1.copy(tempTarget1);
                    if (tempTargetDest2) {
                      tempTargetDest2.copy(tempTarget2);
                    }
                    closestDistance = dist;
                    closestDistanceTriIndex = i;
                    closestDistanceOtherTriIndex = i2;
                  }
                  if (dist < minThreshold) {
                    return true;
                  }
                }
              }
            }
          });
        } else {
          const triCount = getTriCount(otherGeometry);
          for (let i2 = 0, l2 = triCount; i2 < l2; i2++) {
            setTriangle(triangle22, 3 * i2, otherIndex, otherPos);
            triangle22.a.applyMatrix4(geometryToBvh);
            triangle22.b.applyMatrix4(geometryToBvh);
            triangle22.c.applyMatrix4(geometryToBvh);
            triangle22.needsUpdate = true;
            for (let i = offset, l = offset + count; i < l; i++) {
              const ti = bvh.resolveTriangleIndex(i);
              setTriangle(triangle3, 3 * ti, index, pos);
              triangle3.needsUpdate = true;
              const dist = triangle3.distanceToTriangle(triangle22, tempTarget1, tempTarget2);
              if (dist < closestDistance) {
                tempTargetDest1.copy(tempTarget1);
                if (tempTargetDest2) {
                  tempTargetDest2.copy(tempTarget2);
                }
                closestDistance = dist;
                closestDistanceTriIndex = i;
                closestDistanceOtherTriIndex = i2;
              }
              if (dist < minThreshold) {
                return true;
              }
            }
          }
        }
      }
    }
  );
  ExtendedTrianglePool.releasePrimitive(triangle3);
  ExtendedTrianglePool.releasePrimitive(triangle22);
  if (closestDistance === Infinity) {
    return null;
  }
  if (!target1.point) {
    target1.point = tempTargetDest1.clone();
  } else {
    target1.point.copy(tempTargetDest1);
  }
  target1.distance = closestDistance, target1.faceIndex = closestDistanceTriIndex;
  if (target2) {
    if (!target2.point)
      target2.point = tempTargetDest2.clone();
    else
      target2.point.copy(tempTargetDest2);
    target2.point.applyMatrix4(tempMatrix);
    tempTargetDest1.applyMatrix4(tempMatrix);
    target2.distance = tempTargetDest1.sub(target2.point).length();
    target2.faceIndex = closestDistanceOtherTriIndex;
  }
  return target1;
}
function isSharedArrayBufferSupported() {
  return typeof SharedArrayBuffer !== "undefined";
}
const _bufferStack1 = new BufferStack.constructor();
const _bufferStack2 = new BufferStack.constructor();
const _boxPool = new PrimitivePool(() => new Box3());
const _leftBox1 = new Box3();
const _rightBox1 = new Box3();
const _leftBox2 = new Box3();
const _rightBox2 = new Box3();
let _active = false;
function bvhcast(bvh, otherBvh, matrixToLocal, intersectsRanges) {
  if (_active) {
    throw new Error("MeshBVH: Recursive calls to bvhcast not supported.");
  }
  _active = true;
  const roots = bvh._roots;
  const otherRoots = otherBvh._roots;
  let result;
  let offset1 = 0;
  let offset2 = 0;
  const invMat = new Matrix4().copy(matrixToLocal).invert();
  for (let i = 0, il = roots.length; i < il; i++) {
    _bufferStack1.setBuffer(roots[i]);
    offset2 = 0;
    const localBox = _boxPool.getPrimitive();
    arrayToBox(BOUNDING_DATA_INDEX(0), _bufferStack1.float32Array, localBox);
    localBox.applyMatrix4(invMat);
    for (let j = 0, jl = otherRoots.length; j < jl; j++) {
      _bufferStack2.setBuffer(otherRoots[i]);
      result = _traverse(
        0,
        0,
        matrixToLocal,
        invMat,
        intersectsRanges,
        offset1,
        offset2,
        0,
        0,
        localBox
      );
      _bufferStack2.clearBuffer();
      offset2 += otherRoots[j].length;
      if (result) {
        break;
      }
    }
    _boxPool.releasePrimitive(localBox);
    _bufferStack1.clearBuffer();
    offset1 += roots[i].length;
    if (result) {
      break;
    }
  }
  _active = false;
  return result;
}
function _traverse(node1Index32, node2Index32, matrix2to1, matrix1to2, intersectsRangesFunc, node1IndexByteOffset = 0, node2IndexByteOffset = 0, depth1 = 0, depth2 = 0, currBox = null, reversed = false) {
  let bufferStack1, bufferStack2;
  if (reversed) {
    bufferStack1 = _bufferStack2;
    bufferStack2 = _bufferStack1;
  } else {
    bufferStack1 = _bufferStack1;
    bufferStack2 = _bufferStack2;
  }
  const float32Array1 = bufferStack1.float32Array, uint32Array1 = bufferStack1.uint32Array, uint16Array1 = bufferStack1.uint16Array, float32Array2 = bufferStack2.float32Array, uint32Array2 = bufferStack2.uint32Array, uint16Array2 = bufferStack2.uint16Array;
  const node1Index16 = node1Index32 * 2;
  const node2Index16 = node2Index32 * 2;
  const isLeaf1 = IS_LEAF(node1Index16, uint16Array1);
  const isLeaf2 = IS_LEAF(node2Index16, uint16Array2);
  let result = false;
  if (isLeaf2 && isLeaf1) {
    if (reversed) {
      result = intersectsRangesFunc(
        OFFSET(node2Index32, uint32Array2),
        COUNT(node2Index32 * 2, uint16Array2),
        OFFSET(node1Index32, uint32Array1),
        COUNT(node1Index32 * 2, uint16Array1),
        depth2,
        node2IndexByteOffset + node2Index32,
        depth1,
        node1IndexByteOffset + node1Index32
      );
    } else {
      result = intersectsRangesFunc(
        OFFSET(node1Index32, uint32Array1),
        COUNT(node1Index32 * 2, uint16Array1),
        OFFSET(node2Index32, uint32Array2),
        COUNT(node2Index32 * 2, uint16Array2),
        depth1,
        node1IndexByteOffset + node1Index32,
        depth2,
        node2IndexByteOffset + node2Index32
      );
    }
  } else if (isLeaf2) {
    const newBox = _boxPool.getPrimitive();
    arrayToBox(BOUNDING_DATA_INDEX(node2Index32), float32Array2, newBox);
    newBox.applyMatrix4(matrix2to1);
    const cl1 = LEFT_NODE(node1Index32);
    const cr1 = RIGHT_NODE(node1Index32, uint32Array1);
    arrayToBox(BOUNDING_DATA_INDEX(cl1), float32Array1, _leftBox1);
    arrayToBox(BOUNDING_DATA_INDEX(cr1), float32Array1, _rightBox1);
    const intersectCl1 = newBox.intersectsBox(_leftBox1);
    const intersectCr1 = newBox.intersectsBox(_rightBox1);
    result = intersectCl1 && _traverse(
      node2Index32,
      cl1,
      matrix1to2,
      matrix2to1,
      intersectsRangesFunc,
      node2IndexByteOffset,
      node1IndexByteOffset,
      depth2,
      depth1 + 1,
      newBox,
      !reversed
    ) || intersectCr1 && _traverse(
      node2Index32,
      cr1,
      matrix1to2,
      matrix2to1,
      intersectsRangesFunc,
      node2IndexByteOffset,
      node1IndexByteOffset,
      depth2,
      depth1 + 1,
      newBox,
      !reversed
    );
    _boxPool.releasePrimitive(newBox);
  } else {
    const cl2 = LEFT_NODE(node2Index32);
    const cr2 = RIGHT_NODE(node2Index32, uint32Array2);
    arrayToBox(BOUNDING_DATA_INDEX(cl2), float32Array2, _leftBox2);
    arrayToBox(BOUNDING_DATA_INDEX(cr2), float32Array2, _rightBox2);
    const leftIntersects = currBox.intersectsBox(_leftBox2);
    const rightIntersects = currBox.intersectsBox(_rightBox2);
    if (leftIntersects && rightIntersects) {
      result = _traverse(
        node1Index32,
        cl2,
        matrix2to1,
        matrix1to2,
        intersectsRangesFunc,
        node1IndexByteOffset,
        node2IndexByteOffset,
        depth1,
        depth2 + 1,
        currBox,
        reversed
      ) || _traverse(
        node1Index32,
        cr2,
        matrix2to1,
        matrix1to2,
        intersectsRangesFunc,
        node1IndexByteOffset,
        node2IndexByteOffset,
        depth1,
        depth2 + 1,
        currBox,
        reversed
      );
    } else if (leftIntersects) {
      if (isLeaf1) {
        result = _traverse(
          node1Index32,
          cl2,
          matrix2to1,
          matrix1to2,
          intersectsRangesFunc,
          node1IndexByteOffset,
          node2IndexByteOffset,
          depth1,
          depth2 + 1,
          currBox,
          reversed
        );
      } else {
        const newBox = _boxPool.getPrimitive();
        newBox.copy(_leftBox2).applyMatrix4(matrix2to1);
        const cl1 = LEFT_NODE(node1Index32);
        const cr1 = RIGHT_NODE(node1Index32, uint32Array1);
        arrayToBox(BOUNDING_DATA_INDEX(cl1), float32Array1, _leftBox1);
        arrayToBox(BOUNDING_DATA_INDEX(cr1), float32Array1, _rightBox1);
        const intersectCl1 = newBox.intersectsBox(_leftBox1);
        const intersectCr1 = newBox.intersectsBox(_rightBox1);
        result = intersectCl1 && _traverse(
          cl2,
          cl1,
          matrix1to2,
          matrix2to1,
          intersectsRangesFunc,
          node2IndexByteOffset,
          node1IndexByteOffset,
          depth2,
          depth1 + 1,
          newBox,
          !reversed
        ) || intersectCr1 && _traverse(
          cl2,
          cr1,
          matrix1to2,
          matrix2to1,
          intersectsRangesFunc,
          node2IndexByteOffset,
          node1IndexByteOffset,
          depth2,
          depth1 + 1,
          newBox,
          !reversed
        );
        _boxPool.releasePrimitive(newBox);
      }
    } else if (rightIntersects) {
      if (isLeaf1) {
        result = _traverse(
          node1Index32,
          cr2,
          matrix2to1,
          matrix1to2,
          intersectsRangesFunc,
          node1IndexByteOffset,
          node2IndexByteOffset,
          depth1,
          depth2 + 1,
          currBox,
          reversed
        );
      } else {
        const newBox = _boxPool.getPrimitive();
        newBox.copy(_rightBox2).applyMatrix4(matrix2to1);
        const cl1 = LEFT_NODE(node1Index32);
        const cr1 = RIGHT_NODE(node1Index32, uint32Array1);
        arrayToBox(BOUNDING_DATA_INDEX(cl1), float32Array1, _leftBox1);
        arrayToBox(BOUNDING_DATA_INDEX(cr1), float32Array1, _rightBox1);
        const intersectCl1 = newBox.intersectsBox(_leftBox1);
        const intersectCr1 = newBox.intersectsBox(_rightBox1);
        result = intersectCl1 && _traverse(
          cr2,
          cl1,
          matrix1to2,
          matrix2to1,
          intersectsRangesFunc,
          node2IndexByteOffset,
          node1IndexByteOffset,
          depth2,
          depth1 + 1,
          newBox,
          !reversed
        ) || intersectCr1 && _traverse(
          cr2,
          cr1,
          matrix1to2,
          matrix2to1,
          intersectsRangesFunc,
          node2IndexByteOffset,
          node1IndexByteOffset,
          depth2,
          depth1 + 1,
          newBox,
          !reversed
        );
        _boxPool.releasePrimitive(newBox);
      }
    }
  }
  return result;
}
const obb = /* @__PURE__ */ new OrientedBox();
const tempBox = /* @__PURE__ */ new Box3();
class MeshBVH {
  static serialize(bvh, options = {}) {
    options = {
      cloneBuffers: true,
      ...options
    };
    const geometry = bvh.geometry;
    const rootData = bvh._roots;
    const indirectBuffer = bvh._indirectBuffer;
    const indexAttribute = geometry.getIndex();
    let result;
    if (options.cloneBuffers) {
      result = {
        roots: rootData.map((root) => root.slice()),
        index: indexAttribute.array.slice(),
        indirectBuffer: indirectBuffer ? indirectBuffer.slice() : null
      };
    } else {
      result = {
        roots: rootData,
        index: indexAttribute.array,
        indirectBuffer
      };
    }
    return result;
  }
  static deserialize(data, geometry, options = {}) {
    options = {
      setIndex: true,
      indirect: Boolean(data.indirectBuffer),
      ...options
    };
    const { index, roots, indirectBuffer } = data;
    const bvh = new MeshBVH(geometry, { ...options, [SKIP_GENERATION]: true });
    bvh._roots = roots;
    bvh._indirectBuffer = indirectBuffer || null;
    if (options.setIndex) {
      const indexAttribute = geometry.getIndex();
      if (indexAttribute === null) {
        const newIndex = new BufferAttribute(data.index, 1, false);
        geometry.setIndex(newIndex);
      } else if (indexAttribute.array !== index) {
        indexAttribute.array.set(index);
        indexAttribute.needsUpdate = true;
      }
    }
    return bvh;
  }
  get indirect() {
    return !!this._indirectBuffer;
  }
  constructor(geometry, options = {}) {
    if (!geometry.isBufferGeometry) {
      throw new Error("MeshBVH: Only BufferGeometries are supported.");
    } else if (geometry.index && geometry.index.isInterleavedBufferAttribute) {
      throw new Error("MeshBVH: InterleavedBufferAttribute is not supported for the index attribute.");
    }
    options = Object.assign({
      strategy: CENTER,
      maxDepth: 40,
      maxLeafTris: 10,
      verbose: true,
      useSharedArrayBuffer: false,
      setBoundingBox: true,
      onProgress: null,
      indirect: false,
      // undocumented options
      // Whether to skip generating the tree. Used for deserialization.
      [SKIP_GENERATION]: false
    }, options);
    if (options.useSharedArrayBuffer && !isSharedArrayBufferSupported()) {
      throw new Error("MeshBVH: SharedArrayBuffer is not available.");
    }
    this.geometry = geometry;
    this._roots = null;
    this._indirectBuffer = null;
    if (!options[SKIP_GENERATION]) {
      buildPackedTree(this, options);
      if (!geometry.boundingBox && options.setBoundingBox) {
        geometry.boundingBox = this.getBoundingBox(new Box3());
      }
    }
    const { _indirectBuffer } = this;
    this.resolveTriangleIndex = options.indirect ? (i) => _indirectBuffer[i] : (i) => i;
  }
  refit(nodeIndices = null) {
    const refitFunc = this.indirect ? refit_indirect : refit;
    return refitFunc(this, nodeIndices);
  }
  traverse(callback, rootIndex = 0) {
    const buffer = this._roots[rootIndex];
    const uint32Array = new Uint32Array(buffer);
    const uint16Array = new Uint16Array(buffer);
    _traverse2(0);
    function _traverse2(node32Index, depth = 0) {
      const node16Index = node32Index * 2;
      const isLeaf = uint16Array[node16Index + 15] === IS_LEAFNODE_FLAG;
      if (isLeaf) {
        const offset = uint32Array[node32Index + 6];
        const count = uint16Array[node16Index + 14];
        callback(depth, isLeaf, new Float32Array(buffer, node32Index * 4, 6), offset, count);
      } else {
        const left = node32Index + BYTES_PER_NODE / 4;
        const right = uint32Array[node32Index + 6];
        const splitAxis = uint32Array[node32Index + 7];
        const stopTraversal = callback(depth, isLeaf, new Float32Array(buffer, node32Index * 4, 6), splitAxis);
        if (!stopTraversal) {
          _traverse2(left, depth + 1);
          _traverse2(right, depth + 1);
        }
      }
    }
  }
  /* Core Cast Functions */
  raycast(ray2, materialOrSide = FrontSide) {
    const roots = this._roots;
    const geometry = this.geometry;
    const intersects = [];
    const isMaterial = materialOrSide.isMaterial;
    const isArrayMaterial = Array.isArray(materialOrSide);
    const groups = geometry.groups;
    const side = isMaterial ? materialOrSide.side : materialOrSide;
    const raycastFunc = this.indirect ? raycast_indirect : raycast;
    for (let i = 0, l = roots.length; i < l; i++) {
      const materialSide = isArrayMaterial ? materialOrSide[groups[i].materialIndex].side : side;
      const startCount = intersects.length;
      raycastFunc(this, i, materialSide, ray2, intersects);
      if (isArrayMaterial) {
        const materialIndex = groups[i].materialIndex;
        for (let j = startCount, jl = intersects.length; j < jl; j++) {
          intersects[j].face.materialIndex = materialIndex;
        }
      }
    }
    return intersects;
  }
  raycastFirst(ray2, materialOrSide = FrontSide) {
    const roots = this._roots;
    const geometry = this.geometry;
    const isMaterial = materialOrSide.isMaterial;
    const isArrayMaterial = Array.isArray(materialOrSide);
    let closestResult = null;
    const groups = geometry.groups;
    const side = isMaterial ? materialOrSide.side : materialOrSide;
    const raycastFirstFunc = this.indirect ? raycastFirst_indirect : raycastFirst;
    for (let i = 0, l = roots.length; i < l; i++) {
      const materialSide = isArrayMaterial ? materialOrSide[groups[i].materialIndex].side : side;
      const result = raycastFirstFunc(this, i, materialSide, ray2);
      if (result != null && (closestResult == null || result.distance < closestResult.distance)) {
        closestResult = result;
        if (isArrayMaterial) {
          result.face.materialIndex = groups[i].materialIndex;
        }
      }
    }
    return closestResult;
  }
  intersectsGeometry(otherGeometry, geomToMesh) {
    let result = false;
    const roots = this._roots;
    const intersectsGeometryFunc = this.indirect ? intersectsGeometry_indirect : intersectsGeometry;
    for (let i = 0, l = roots.length; i < l; i++) {
      result = intersectsGeometryFunc(this, i, otherGeometry, geomToMesh);
      if (result) {
        break;
      }
    }
    return result;
  }
  shapecast(callbacks) {
    const triangle3 = ExtendedTrianglePool.getPrimitive();
    const iterateFunc = this.indirect ? iterateOverTriangles_indirect : iterateOverTriangles;
    let {
      boundsTraverseOrder,
      intersectsBounds,
      intersectsRange,
      intersectsTriangle
    } = callbacks;
    if (intersectsRange && intersectsTriangle) {
      const originalIntersectsRange = intersectsRange;
      intersectsRange = (offset, count, contained, depth, nodeIndex) => {
        if (!originalIntersectsRange(offset, count, contained, depth, nodeIndex)) {
          return iterateFunc(offset, count, this, intersectsTriangle, contained, depth, triangle3);
        }
        return true;
      };
    } else if (!intersectsRange) {
      if (intersectsTriangle) {
        intersectsRange = (offset, count, contained, depth) => {
          return iterateFunc(offset, count, this, intersectsTriangle, contained, depth, triangle3);
        };
      } else {
        intersectsRange = (offset, count, contained) => {
          return contained;
        };
      }
    }
    let result = false;
    let byteOffset = 0;
    const roots = this._roots;
    for (let i = 0, l = roots.length; i < l; i++) {
      const root = roots[i];
      result = shapecast(this, i, intersectsBounds, intersectsRange, boundsTraverseOrder, byteOffset);
      if (result) {
        break;
      }
      byteOffset += root.byteLength;
    }
    ExtendedTrianglePool.releasePrimitive(triangle3);
    return result;
  }
  bvhcast(otherBvh, matrixToLocal, callbacks) {
    let {
      intersectsRanges,
      intersectsTriangles
    } = callbacks;
    const triangle1 = ExtendedTrianglePool.getPrimitive();
    const indexAttr1 = this.geometry.index;
    const positionAttr1 = this.geometry.attributes.position;
    const assignTriangle1 = this.indirect ? (i1) => {
      const ti = this.resolveTriangleIndex(i1);
      setTriangle(triangle1, ti * 3, indexAttr1, positionAttr1);
    } : (i1) => {
      setTriangle(triangle1, i1 * 3, indexAttr1, positionAttr1);
    };
    const triangle22 = ExtendedTrianglePool.getPrimitive();
    const indexAttr2 = otherBvh.geometry.index;
    const positionAttr2 = otherBvh.geometry.attributes.position;
    const assignTriangle2 = otherBvh.indirect ? (i2) => {
      const ti2 = otherBvh.resolveTriangleIndex(i2);
      setTriangle(triangle22, ti2 * 3, indexAttr2, positionAttr2);
    } : (i2) => {
      setTriangle(triangle22, i2 * 3, indexAttr2, positionAttr2);
    };
    if (intersectsTriangles) {
      const iterateOverDoubleTriangles = (offset1, count1, offset2, count2, depth1, index1, depth2, index2) => {
        for (let i2 = offset2, l2 = offset2 + count2; i2 < l2; i2++) {
          assignTriangle2(i2);
          triangle22.a.applyMatrix4(matrixToLocal);
          triangle22.b.applyMatrix4(matrixToLocal);
          triangle22.c.applyMatrix4(matrixToLocal);
          triangle22.needsUpdate = true;
          for (let i1 = offset1, l1 = offset1 + count1; i1 < l1; i1++) {
            assignTriangle1(i1);
            triangle1.needsUpdate = true;
            if (intersectsTriangles(triangle1, triangle22, i1, i2, depth1, index1, depth2, index2)) {
              return true;
            }
          }
        }
        return false;
      };
      if (intersectsRanges) {
        const originalIntersectsRanges = intersectsRanges;
        intersectsRanges = function(offset1, count1, offset2, count2, depth1, index1, depth2, index2) {
          if (!originalIntersectsRanges(offset1, count1, offset2, count2, depth1, index1, depth2, index2)) {
            return iterateOverDoubleTriangles(offset1, count1, offset2, count2, depth1, index1, depth2, index2);
          }
          return true;
        };
      } else {
        intersectsRanges = iterateOverDoubleTriangles;
      }
    }
    return bvhcast(this, otherBvh, matrixToLocal, intersectsRanges);
  }
  /* Derived Cast Functions */
  intersectsBox(box, boxToMesh) {
    obb.set(box.min, box.max, boxToMesh);
    obb.needsUpdate = true;
    return this.shapecast(
      {
        intersectsBounds: (box2) => obb.intersectsBox(box2),
        intersectsTriangle: (tri) => obb.intersectsTriangle(tri)
      }
    );
  }
  intersectsSphere(sphere) {
    return this.shapecast(
      {
        intersectsBounds: (box) => sphere.intersectsBox(box),
        intersectsTriangle: (tri) => tri.intersectsSphere(sphere)
      }
    );
  }
  closestPointToGeometry(otherGeometry, geometryToBvh, target1 = {}, target2 = {}, minThreshold = 0, maxThreshold = Infinity) {
    const closestPointToGeometryFunc = this.indirect ? closestPointToGeometry_indirect : closestPointToGeometry;
    return closestPointToGeometryFunc(
      this,
      otherGeometry,
      geometryToBvh,
      target1,
      target2,
      minThreshold,
      maxThreshold
    );
  }
  closestPointToPoint(point, target = {}, minThreshold = 0, maxThreshold = Infinity) {
    return closestPointToPoint(
      this,
      point,
      target,
      minThreshold,
      maxThreshold
    );
  }
  getBoundingBox(target) {
    target.makeEmpty();
    const roots = this._roots;
    roots.forEach((buffer) => {
      arrayToBox(0, new Float32Array(buffer), tempBox);
      target.union(tempBox);
    });
    return target;
  }
}
function convertRaycastIntersect(hit, object, raycaster) {
  if (hit === null) {
    return null;
  }
  hit.point.applyMatrix4(object.matrixWorld);
  hit.distance = hit.point.distanceTo(raycaster.ray.origin);
  hit.object = object;
  if (hit.distance < raycaster.near || hit.distance > raycaster.far) {
    return null;
  } else {
    return hit;
  }
}
const ray = /* @__PURE__ */ new Ray();
const tmpInverseMatrix = /* @__PURE__ */ new Matrix4();
const origMeshRaycastFunc = Mesh.prototype.raycast;
function acceleratedRaycast(raycaster, intersects) {
  if (this.geometry.boundsTree) {
    if (this.material === void 0)
      return;
    tmpInverseMatrix.copy(this.matrixWorld).invert();
    ray.copy(raycaster.ray).applyMatrix4(tmpInverseMatrix);
    const bvh = this.geometry.boundsTree;
    if (raycaster.firstHitOnly === true) {
      const hit = convertRaycastIntersect(bvh.raycastFirst(ray, this.material), this, raycaster);
      if (hit) {
        intersects.push(hit);
      }
    } else {
      const hits = bvh.raycast(ray, this.material);
      for (let i = 0, l = hits.length; i < l; i++) {
        const hit = convertRaycastIntersect(hits[i], this, raycaster);
        if (hit) {
          intersects.push(hit);
        }
      }
    }
  } else {
    origMeshRaycastFunc.call(this, raycaster, intersects);
  }
}
function computeBoundsTree(options) {
  this.boundsTree = new MeshBVH(this, options);
  return this.boundsTree;
}
function disposeBoundsTree() {
  this.boundsTree = null;
}
const _BVH = class _BVH {
  /**
   * Applies the Bounding Volume Hierarchy (BVH) to a given BufferGeometry.
   * If the BVH is not already initialized, it adds the necessary methods to the BufferGeometry and Mesh prototypes.
   * If the geometry does not have a boundsTree, it computes one.
   *
   * @param geometry - The BufferGeometry to apply the BVH to.
   */
  static apply(geometry) {
    if (!_BVH.initialized) {
      BufferGeometry.prototype.computeBoundsTree = computeBoundsTree;
      BufferGeometry.prototype.disposeBoundsTree = disposeBoundsTree;
      Mesh.prototype.raycast = acceleratedRaycast;
      _BVH.initialized = true;
    }
    if (!geometry.boundsTree) {
      geometry.computeBoundsTree();
    }
  }
  /**
   * Disposes of the BVH associated with the given BufferGeometry.
   * If the geometry has a boundsTree, it disposes of it.
   *
   * @param geometry - The BufferGeometry to dispose of the BVH from.
   */
  static dispose(geometry) {
    if (geometry && geometry.disposeBoundsTree) {
      geometry.disposeBoundsTree();
    }
  }
};
/**
 * A flag indicating whether the BVH has been initialized.
 * Initialized means the necessary methods have been added to BufferGeometry and Mesh prototypes.
 */
__publicField(_BVH, "initialized", false);
let BVH = _BVH;
let Fragment$2 = class Fragment {
  /**
   * Constructs a new Fragment.
   * @param geometry - The geometry of the fragment.
   * @param material - The material(s) of the fragment.
   * @param count - The initial number of instances in the fragment.
   */
  constructor(geometry, material, count) {
    /**
     * A set of unique item IDs associated with this fragment.
     */
    __publicField(this, "ids", /* @__PURE__ */ new Set());
    /**
     * A map of item IDs to sets of instance IDs.
     */
    __publicField(this, "itemToInstances", /* @__PURE__ */ new Map());
    /**
     * A map of instance IDs to item IDs.
     */
    __publicField(this, "instanceToItem", /* @__PURE__ */ new Map());
    /**
     * A set of item IDs of instances that are currently hidden.
     */
    __publicField(this, "hiddenItems", /* @__PURE__ */ new Set());
    /**
     * The unique identifier of this fragment.
     */
    __publicField(this, "id");
    /**
     * The mesh associated with this fragment.
     */
    __publicField(this, "mesh");
    /**
     * The amount of instances that this fragment can contain.
     */
    __publicField(this, "capacity", 0);
    /**
     * The amount by which to increase the capacity when necessary.
     */
    __publicField(this, "capacityOffset", 10);
    /**
     * The group of fragments to which this fragment belongs.
     */
    __publicField(this, "group");
    __publicField(this, "_originalColors", /* @__PURE__ */ new Map());
    __publicField(this, "_settingVisibility", false);
    this.mesh = new FragmentMesh(geometry, material, count, this);
    this.id = this.mesh.uuid;
    this.capacity = count;
    this.mesh.count = 0;
    if (this.mesh.geometry.index.count) {
      BVH.apply(this.mesh.geometry);
    }
  }
  /**
   * A getter property that returns the unique vertices of the fragment's geometry.
   * The unique vertices are determined by comparing the vertex positions.
   *
   * @returns An array of unique vertices.
   */
  get uniqueVertices() {
    const uniqueVertices = [];
    const position = this.mesh.geometry.getAttribute(
      "position"
    );
    if (!position)
      return uniqueVertices;
    const uniqueVerticesSet = /* @__PURE__ */ new Set();
    for (let i = 0; i < position.count; i++) {
      const x = position.getX(i);
      const y = position.getY(i);
      const z = position.getZ(i);
      const vertexKey = `${x},${y},${z}`;
      if (!uniqueVerticesSet.has(vertexKey)) {
        uniqueVerticesSet.add(vertexKey);
        uniqueVertices.push(new THREE.Vector3(x, y, z));
      }
    }
    return uniqueVertices;
  }
  /**
   * Disposes of the fragment and its associated resources.
   *
   * @param disposeResources - If true, disposes geometries and materials associated with the fragment. If false, only disposes of the fragment itself.
   */
  dispose(disposeResources = true) {
    this.clear();
    this.group = void 0;
    this._originalColors.clear();
    if (this.mesh) {
      if (disposeResources) {
        for (const mat of this.mesh.material) {
          mat.dispose();
        }
        this.mesh.material = [];
        BVH.dispose(this.mesh.geometry);
        if (this.mesh.geometry) {
          this.mesh.geometry.dispose();
        }
        this.mesh.geometry = null;
      }
      this.mesh.removeFromParent();
      this.mesh.userData = {};
      this.mesh.dispose();
      this.mesh.fragment = null;
      this.mesh = null;
    }
  }
  /**
   * Retrieves the transform matrices and colors of instances associated with a given item ID.
   *
   * @param itemID - The unique identifier of the item.
   * @throws Will throw an error if the item is not found.
   * @returns An object containing the item ID, an array of transform matrices, and an optional array of colors.
   * If no colors are found, the colors array will be undefined.
   */
  get(itemID) {
    const instanceIDs = this.getInstancesIDs(itemID);
    if (!instanceIDs) {
      throw new Error("Item not found!");
    }
    const transforms = [];
    const colorsArray = [];
    for (const id of instanceIDs) {
      const matrix = new THREE.Matrix4();
      this.mesh.getMatrixAt(id, matrix);
      transforms.push(matrix);
      if (this.mesh.instanceColor) {
        const color = new THREE.Color();
        this.mesh.getColorAt(id, color);
        colorsArray.push(color);
      }
    }
    const colors = colorsArray.length ? colorsArray : void 0;
    return { id: itemID, transforms, colors };
  }
  /**
   * Retrieves the item ID associated with a given instance ID.
   *
   * @param instanceID - The unique identifier of the instance.
   * @returns The item ID associated with the instance, or null if no association exists.
   */
  getItemID(instanceID) {
    return this.instanceToItem.get(instanceID) || null;
  }
  /**
   * Retrieves the instance IDs associated with a given item ID.
   *
   * @param itemID - The unique identifier of the item.
   * @returns The set of instance IDs associated with the item, or null if no association exists.
   */
  getInstancesIDs(itemID) {
    return this.itemToInstances.get(itemID) || null;
  }
  /**
   * Updates the instance color and matrix attributes of the fragment's mesh.
   * This method should be called whenever the instance color or matrix attributes
   * need to be updated.
   */
  update() {
    if (this.mesh.instanceColor) {
      this.mesh.instanceColor.needsUpdate = true;
    }
    this.mesh.instanceMatrix.needsUpdate = true;
  }
  /**
   * Adds items to the fragment.
   *
   * @param items - An array of items to be added. Each item contains an ID, an array of transform matrices, and an optional array of colors.
   *
   * If the necessary capacity to accommodate the new items exceeds the current capacity,
   * a new mesh with a larger capacity is created, and the old mesh is disposed.
   *
   * The transform matrices and colors of the items are added to the respective attributes of the mesh.
   *
   * The instance IDs, item IDs, and associations between instance IDs and item IDs are updated accordingly.
   *
   * The instance color and matrix attributes of the mesh are updated.
   */
  add(items) {
    var _a;
    let size = 0;
    for (const item of items) {
      size += item.transforms.length;
    }
    const necessaryCapacity = this.mesh.count + size;
    if (necessaryCapacity > this.capacity) {
      const newCapacity = necessaryCapacity + this.capacityOffset;
      const newMesh = new FragmentMesh(
        this.mesh.geometry,
        this.mesh.material,
        newCapacity,
        this
      );
      newMesh.count = this.mesh.count;
      this.capacity = newCapacity;
      const oldMesh = this.mesh;
      (_a = oldMesh.parent) == null ? void 0 : _a.add(newMesh);
      oldMesh.removeFromParent();
      this.mesh = newMesh;
      const tempMatrix2 = new THREE.Matrix4();
      for (let i = 0; i < oldMesh.instanceMatrix.count; i++) {
        oldMesh.getMatrixAt(i, tempMatrix2);
        newMesh.setMatrixAt(i, tempMatrix2);
      }
      if (oldMesh.instanceColor) {
        const tempColor = new THREE.Color();
        for (let i = 0; i < oldMesh.instanceColor.count; i++) {
          oldMesh.getColorAt(i, tempColor);
          newMesh.setColorAt(i, tempColor);
        }
      }
      oldMesh.dispose();
    }
    for (let i = 0; i < items.length; i++) {
      const { transforms, colors, id } = items[i];
      if (!this.itemToInstances.has(id)) {
        this.itemToInstances.set(id, /* @__PURE__ */ new Set());
      }
      const instances = this.itemToInstances.get(id);
      this.ids.add(id);
      for (let j = 0; j < transforms.length; j++) {
        const transform = transforms[j];
        const newInstanceID = this.mesh.count;
        this.mesh.setMatrixAt(newInstanceID, transform);
        if (colors) {
          const color = colors[j];
          this.mesh.setColorAt(newInstanceID, color);
        }
        instances.add(newInstanceID);
        this.instanceToItem.set(newInstanceID, id);
        this.mesh.count++;
      }
    }
    this.update();
  }
  /**
   * Removes items from the fragment.
   *
   * @param itemsIDs - An iterable of item IDs to be removed.
   *
   * The instance IDs, item IDs, and associations between instance IDs and item IDs are updated accordingly.
   *
   * The instance color and matrix attributes of the mesh are updated.
   *
   * @throws Will throw an error if the instances are not found.
   */
  remove(itemsIDs) {
    if (this.mesh.count === 0) {
      return;
    }
    for (const itemID of itemsIDs) {
      const instancesToDelete = this.itemToInstances.get(itemID);
      if (instancesToDelete === void 0) {
        throw new Error("Instances not found!");
      }
      for (const instanceID of instancesToDelete) {
        if (this.mesh.count === 0)
          throw new Error("Error with mesh count!");
        this.putLast(instanceID);
        this.instanceToItem.delete(instanceID);
        this.mesh.count--;
      }
      this.itemToInstances.delete(itemID);
      this.ids.delete(itemID);
    }
    this.update();
  }
  /**
   * Clears the fragment by resetting the hidden items, item IDs, instance-to-item associations,
   * instance-to-item map, and the count of instances in the fragment's mesh.
   *
   * @remarks
   * This method is used to reset the fragment to its initial state.
   *
   * @example
   * ```typescript
   * fragment.clear();
   * ```
   */
  clear() {
    this.hiddenItems.clear();
    this.ids.clear();
    this.instanceToItem.clear();
    this.itemToInstances.clear();
    this.mesh.count = 0;
  }
  /**
   * Sets the visibility of items in the fragment.
   *
   * @param visible - A boolean indicating whether the items should be visible or hidden.
   * @param itemIDs - An iterable of item IDs to be affected. If not provided, all items in the fragment will be affected.
   *
   * @remarks
   * This method updates the visibility of items in the fragment based on the provided visibility flag.
   *
   *
   * @example
   * ```typescript
   * fragment.setVisibility(true, [1, 2, 3]); // Makes items with IDs 1, 2, and 3 visible.
   * fragment.setVisibility(false); // Makes all items in the fragment hidden.
   * ```
   */
  setVisibility(visible, itemIDs = this.ids) {
    if (this._settingVisibility)
      return;
    this._settingVisibility = true;
    if (visible) {
      for (const itemID of itemIDs) {
        if (!this.ids.has(itemID)) {
          continue;
        }
        if (!this.hiddenItems.has(itemID)) {
          continue;
        }
        const instances = this.itemToInstances.get(itemID);
        if (!instances)
          throw new Error("Instances not found!");
        for (const instance of new Set(instances)) {
          this.mesh.count++;
          this.putLast(instance);
        }
        this.hiddenItems.delete(itemID);
      }
    } else {
      for (const itemID of itemIDs) {
        if (!this.ids.has(itemID)) {
          continue;
        }
        if (this.hiddenItems.has(itemID)) {
          continue;
        }
        const instances = this.itemToInstances.get(itemID);
        if (!instances) {
          throw new Error("Instances not found!");
        }
        for (const instance of new Set(instances)) {
          this.putLast(instance);
          this.mesh.count--;
        }
        this.hiddenItems.add(itemID);
      }
    }
    this.update();
    this._settingVisibility = false;
  }
  /**
   * Sets the color of items in the fragment.
   *
   * @param color - The color to be set for the items.
   * @param itemIDs - An iterable of item IDs to be affected. If not provided, all items in the fragment will be affected.
   * @param override - A boolean indicating whether the original color should be overridden. If true, the original color will be replaced with the new color.
   *
   *
   * @example
   * ```typescript
   * fragment.setColor(new THREE.Color(0xff0000), [1, 2, 3], true); // Sets the color of items with IDs 1, 2, and 3 to red, overriding their original colors.
   * fragment.setColor(new THREE.Color(0x00ff00)); // Sets the color of all items in the fragment to green.
   * ```
   */
  setColor(color, itemIDs = this.ids, override = false) {
    if (!this.mesh.instanceColor) {
      throw new Error("This fragment doesn't have color per instance!");
    }
    for (const itemID of itemIDs) {
      if (!this.ids.has(itemID)) {
        continue;
      }
      const instances = this.itemToInstances.get(itemID);
      if (!instances) {
        throw new Error("Instances not found!");
      }
      const originalsExist = this._originalColors.has(itemID);
      if (!originalsExist) {
        this._originalColors.set(itemID, /* @__PURE__ */ new Map());
      }
      const originals = this._originalColors.get(itemID);
      for (const instance of new Set(instances)) {
        if (!originalsExist) {
          const originalColor = new THREE.Color();
          this.mesh.getColorAt(instance, originalColor);
          originals.set(instance, originalColor);
        }
        this.mesh.setColorAt(instance, color);
        if (override) {
          originals.set(instance, color);
        }
      }
    }
    this.mesh.instanceColor.needsUpdate = true;
  }
  /**
   * Resets the color of items in the fragment to their original colors.
   *
   * @param itemIDs - An iterable of item IDs to be affected. If not provided, all items in the fragment will be affected.
   *
   *
   * @example
   * ```typescript
   * fragment.resetColor([1, 2, 3]); // Resets the color of items with IDs 1, 2, and 3 to their original colors.
   * fragment.resetColor(); // Resets the color of all items in the fragment to their original colors.
   * ```
   */
  resetColor(itemIDs = this.ids) {
    if (!this.mesh.instanceColor) {
      throw new Error("This fragment doesn't have color per instance!");
    }
    for (const itemID of itemIDs) {
      if (!this.ids.has(itemID)) {
        continue;
      }
      const instances = this.itemToInstances.get(itemID);
      if (!instances) {
        throw new Error("Instances not found!");
      }
      const originals = this._originalColors.get(itemID);
      if (!originals) {
        continue;
      }
      for (const instance of new Set(instances)) {
        const originalColor = originals.get(instance);
        if (!originalColor) {
          throw new Error("Original color not found!");
        }
        this.mesh.setColorAt(instance, originalColor);
      }
    }
    this.mesh.instanceColor.needsUpdate = true;
  }
  /**
   * Applies a transformation matrix to instances associated with given item IDs.
   *
   * @param itemIDs - An iterable of item IDs to be affected.
   * @param transform - The transformation matrix to be applied.
   *
   * @remarks
   * This method applies the provided transformation matrix to the instances associated with the given item IDs.
   *
   * @example
   * ```typescript
   * fragment.applyTransform([1, 2, 3], new THREE.Matrix4().makeTranslation(1, 0, 0)); // Applies a translation of (1, 0, 0) to instances with IDs 1, 2, and 3.
   * ```
   */
  applyTransform(itemIDs, transform) {
    const tempMatrix2 = new THREE.Matrix4();
    for (const itemID of itemIDs) {
      const instances = this.getInstancesIDs(itemID);
      if (instances === null) {
        continue;
      }
      for (const instanceID of instances) {
        this.mesh.getMatrixAt(instanceID, tempMatrix2);
        tempMatrix2.premultiply(transform);
        this.mesh.setMatrixAt(instanceID, tempMatrix2);
      }
    }
    this.update();
  }
  /**
   * Exports the fragment's geometry and associated data.
   *
   * @returns An object containing the exported geometry, an array of IDs associated with the fragment, and the fragment's ID.
   *
   * @remarks
   * This method is used to export the fragment's geometry and associated data for further processing or storage.
   *
   * @example
   * ```typescript
   * const exportedData = fragment.exportData();
   * // Use the exportedData object for further processing or storage
   * ```
   */
  exportData() {
    const geometry = this.mesh.exportData();
    const ids = Array.from(this.ids);
    const id = this.id;
    return { ...geometry, ids, id };
  }
  /**
   * Creates a copy of the whole fragment or a part of it. It shares the geometry with the original fragment, but has its own InstancedMesh data, so it also needs to be disposed.
   *
   * @param itemIDs - An iterable of item IDs to be included in the clone.
   *
   */
  clone(itemIDs = this.ids) {
    const newFragment = new Fragment(
      this.mesh.geometry,
      this.mesh.material,
      this.capacity
    );
    const items = [];
    for (const id of itemIDs) {
      const instancesIDs = this.getInstancesIDs(id);
      if (instancesIDs === null) {
        continue;
      }
      const transforms = [];
      const colors = [];
      for (const instanceID of instancesIDs) {
        const newMatrix = new THREE.Matrix4();
        const newColor = new THREE.Color();
        this.mesh.getMatrixAt(instanceID, newMatrix);
        this.mesh.getColorAt(instanceID, newColor);
        transforms.push(newMatrix);
        colors.push(newColor);
      }
      items.push({
        id,
        transforms,
        colors
      });
    }
    newFragment.add(items);
    return newFragment;
  }
  putLast(instanceID1) {
    if (this.mesh.count === 0)
      return;
    const id1 = this.instanceToItem.get(instanceID1);
    const instanceID2 = this.mesh.count - 1;
    if (instanceID2 === instanceID1) {
      return;
    }
    const id2 = this.instanceToItem.get(instanceID2);
    if (id1 === void 0 || id2 === void 0) {
      throw new Error("Keys not found");
    }
    if (id1 !== id2) {
      const instances1 = this.itemToInstances.get(id1);
      const instances2 = this.itemToInstances.get(id2);
      if (!instances1 || !instances2) {
        throw new Error("Instances not found");
      }
      if (!instances1.has(instanceID1) || !instances2.has(instanceID2)) {
        throw new Error("Malformed fragment structure");
      }
      instances1.delete(instanceID1);
      instances2.delete(instanceID2);
      instances1.add(instanceID2);
      instances2.add(instanceID1);
      this.instanceToItem.set(instanceID1, id2);
      this.instanceToItem.set(instanceID2, id1);
    }
    const transform1 = new THREE.Matrix4();
    const transform2 = new THREE.Matrix4();
    this.mesh.getMatrixAt(instanceID1, transform1);
    this.mesh.getMatrixAt(instanceID2, transform2);
    this.mesh.setMatrixAt(instanceID1, transform2);
    this.mesh.setMatrixAt(instanceID2, transform1);
    if (this.mesh.instanceColor !== null) {
      const color1 = new THREE.Color();
      const color2 = new THREE.Color();
      this.mesh.getColorAt(instanceID1, color1);
      this.mesh.getColorAt(instanceID2, color2);
      this.mesh.setColorAt(instanceID1, color2);
      this.mesh.setColorAt(instanceID2, color1);
      const originals1 = this._originalColors.get(id1);
      if (originals1) {
        const color12 = originals1.get(instanceID1);
        if (color12) {
          originals1.delete(instanceID1);
          originals1.set(instanceID2, color12);
        }
      }
      const originals2 = this._originalColors.get(id2);
      if (originals2) {
        const color22 = originals2.get(instanceID2);
        if (color22) {
          originals2.delete(instanceID2);
          originals2.set(instanceID1, color22);
        }
      }
    }
  }
};
const SIZEOF_SHORT = 2;
const SIZEOF_INT = 4;
const FILE_IDENTIFIER_LENGTH = 4;
const SIZE_PREFIX_LENGTH = 4;
const int32 = new Int32Array(2);
const float32 = new Float32Array(int32.buffer);
const float64 = new Float64Array(int32.buffer);
const isLittleEndian = new Uint16Array(new Uint8Array([1, 0]).buffer)[0] === 1;
var Encoding;
(function(Encoding2) {
  Encoding2[Encoding2["UTF8_BYTES"] = 1] = "UTF8_BYTES";
  Encoding2[Encoding2["UTF16_STRING"] = 2] = "UTF16_STRING";
})(Encoding || (Encoding = {}));
class ByteBuffer {
  /**
   * Create a new ByteBuffer with a given array of bytes (`Uint8Array`)
   */
  constructor(bytes_) {
    this.bytes_ = bytes_;
    this.position_ = 0;
    this.text_decoder_ = new TextDecoder();
  }
  /**
   * Create and allocate a new ByteBuffer with a given size.
   */
  static allocate(byte_size) {
    return new ByteBuffer(new Uint8Array(byte_size));
  }
  clear() {
    this.position_ = 0;
  }
  /**
   * Get the underlying `Uint8Array`.
   */
  bytes() {
    return this.bytes_;
  }
  /**
   * Get the buffer's position.
   */
  position() {
    return this.position_;
  }
  /**
   * Set the buffer's position.
   */
  setPosition(position) {
    this.position_ = position;
  }
  /**
   * Get the buffer's capacity.
   */
  capacity() {
    return this.bytes_.length;
  }
  readInt8(offset) {
    return this.readUint8(offset) << 24 >> 24;
  }
  readUint8(offset) {
    return this.bytes_[offset];
  }
  readInt16(offset) {
    return this.readUint16(offset) << 16 >> 16;
  }
  readUint16(offset) {
    return this.bytes_[offset] | this.bytes_[offset + 1] << 8;
  }
  readInt32(offset) {
    return this.bytes_[offset] | this.bytes_[offset + 1] << 8 | this.bytes_[offset + 2] << 16 | this.bytes_[offset + 3] << 24;
  }
  readUint32(offset) {
    return this.readInt32(offset) >>> 0;
  }
  readInt64(offset) {
    return BigInt.asIntN(64, BigInt(this.readUint32(offset)) + (BigInt(this.readUint32(offset + 4)) << BigInt(32)));
  }
  readUint64(offset) {
    return BigInt.asUintN(64, BigInt(this.readUint32(offset)) + (BigInt(this.readUint32(offset + 4)) << BigInt(32)));
  }
  readFloat32(offset) {
    int32[0] = this.readInt32(offset);
    return float32[0];
  }
  readFloat64(offset) {
    int32[isLittleEndian ? 0 : 1] = this.readInt32(offset);
    int32[isLittleEndian ? 1 : 0] = this.readInt32(offset + 4);
    return float64[0];
  }
  writeInt8(offset, value) {
    this.bytes_[offset] = value;
  }
  writeUint8(offset, value) {
    this.bytes_[offset] = value;
  }
  writeInt16(offset, value) {
    this.bytes_[offset] = value;
    this.bytes_[offset + 1] = value >> 8;
  }
  writeUint16(offset, value) {
    this.bytes_[offset] = value;
    this.bytes_[offset + 1] = value >> 8;
  }
  writeInt32(offset, value) {
    this.bytes_[offset] = value;
    this.bytes_[offset + 1] = value >> 8;
    this.bytes_[offset + 2] = value >> 16;
    this.bytes_[offset + 3] = value >> 24;
  }
  writeUint32(offset, value) {
    this.bytes_[offset] = value;
    this.bytes_[offset + 1] = value >> 8;
    this.bytes_[offset + 2] = value >> 16;
    this.bytes_[offset + 3] = value >> 24;
  }
  writeInt64(offset, value) {
    this.writeInt32(offset, Number(BigInt.asIntN(32, value)));
    this.writeInt32(offset + 4, Number(BigInt.asIntN(32, value >> BigInt(32))));
  }
  writeUint64(offset, value) {
    this.writeUint32(offset, Number(BigInt.asUintN(32, value)));
    this.writeUint32(offset + 4, Number(BigInt.asUintN(32, value >> BigInt(32))));
  }
  writeFloat32(offset, value) {
    float32[0] = value;
    this.writeInt32(offset, int32[0]);
  }
  writeFloat64(offset, value) {
    float64[0] = value;
    this.writeInt32(offset, int32[isLittleEndian ? 0 : 1]);
    this.writeInt32(offset + 4, int32[isLittleEndian ? 1 : 0]);
  }
  /**
   * Return the file identifier.   Behavior is undefined for FlatBuffers whose
   * schema does not include a file_identifier (likely points at padding or the
   * start of a the root vtable).
   */
  getBufferIdentifier() {
    if (this.bytes_.length < this.position_ + SIZEOF_INT + FILE_IDENTIFIER_LENGTH) {
      throw new Error("FlatBuffers: ByteBuffer is too short to contain an identifier.");
    }
    let result = "";
    for (let i = 0; i < FILE_IDENTIFIER_LENGTH; i++) {
      result += String.fromCharCode(this.readInt8(this.position_ + SIZEOF_INT + i));
    }
    return result;
  }
  /**
   * Look up a field in the vtable, return an offset into the object, or 0 if the
   * field is not present.
   */
  __offset(bb_pos, vtable_offset) {
    const vtable = bb_pos - this.readInt32(bb_pos);
    return vtable_offset < this.readInt16(vtable) ? this.readInt16(vtable + vtable_offset) : 0;
  }
  /**
   * Initialize any Table-derived type to point to the union at the given offset.
   */
  __union(t, offset) {
    t.bb_pos = offset + this.readInt32(offset);
    t.bb = this;
    return t;
  }
  /**
   * Create a JavaScript string from UTF-8 data stored inside the FlatBuffer.
   * This allocates a new string and converts to wide chars upon each access.
   *
   * To avoid the conversion to string, pass Encoding.UTF8_BYTES as the
   * "optionalEncoding" argument. This is useful for avoiding conversion when
   * the data will just be packaged back up in another FlatBuffer later on.
   *
   * @param offset
   * @param opt_encoding Defaults to UTF16_STRING
   */
  __string(offset, opt_encoding) {
    offset += this.readInt32(offset);
    const length = this.readInt32(offset);
    offset += SIZEOF_INT;
    const utf8bytes = this.bytes_.subarray(offset, offset + length);
    if (opt_encoding === Encoding.UTF8_BYTES)
      return utf8bytes;
    else
      return this.text_decoder_.decode(utf8bytes);
  }
  /**
   * Handle unions that can contain string as its member, if a Table-derived type then initialize it,
   * if a string then return a new one
   *
   * WARNING: strings are immutable in JS so we can't change the string that the user gave us, this
   * makes the behaviour of __union_with_string different compared to __union
   */
  __union_with_string(o, offset) {
    if (typeof o === "string") {
      return this.__string(offset);
    }
    return this.__union(o, offset);
  }
  /**
   * Retrieve the relative offset stored at "offset"
   */
  __indirect(offset) {
    return offset + this.readInt32(offset);
  }
  /**
   * Get the start of data of a vector whose offset is stored at "offset" in this object.
   */
  __vector(offset) {
    return offset + this.readInt32(offset) + SIZEOF_INT;
  }
  /**
   * Get the length of a vector whose offset is stored at "offset" in this object.
   */
  __vector_len(offset) {
    return this.readInt32(offset + this.readInt32(offset));
  }
  __has_identifier(ident) {
    if (ident.length != FILE_IDENTIFIER_LENGTH) {
      throw new Error("FlatBuffers: file identifier must be length " + FILE_IDENTIFIER_LENGTH);
    }
    for (let i = 0; i < FILE_IDENTIFIER_LENGTH; i++) {
      if (ident.charCodeAt(i) != this.readInt8(this.position() + SIZEOF_INT + i)) {
        return false;
      }
    }
    return true;
  }
  /**
   * A helper function for generating list for obj api
   */
  createScalarList(listAccessor, listLength) {
    const ret = [];
    for (let i = 0; i < listLength; ++i) {
      const val = listAccessor(i);
      if (val !== null) {
        ret.push(val);
      }
    }
    return ret;
  }
  /**
   * A helper function for generating list for obj api
   * @param listAccessor function that accepts an index and return data at that index
   * @param listLength listLength
   * @param res result list
   */
  createObjList(listAccessor, listLength) {
    const ret = [];
    for (let i = 0; i < listLength; ++i) {
      const val = listAccessor(i);
      if (val !== null) {
        ret.push(val.unpack());
      }
    }
    return ret;
  }
}
class Builder {
  /**
   * Create a FlatBufferBuilder.
   */
  constructor(opt_initial_size) {
    this.minalign = 1;
    this.vtable = null;
    this.vtable_in_use = 0;
    this.isNested = false;
    this.object_start = 0;
    this.vtables = [];
    this.vector_num_elems = 0;
    this.force_defaults = false;
    this.string_maps = null;
    this.text_encoder = new TextEncoder();
    let initial_size;
    if (!opt_initial_size) {
      initial_size = 1024;
    } else {
      initial_size = opt_initial_size;
    }
    this.bb = ByteBuffer.allocate(initial_size);
    this.space = initial_size;
  }
  clear() {
    this.bb.clear();
    this.space = this.bb.capacity();
    this.minalign = 1;
    this.vtable = null;
    this.vtable_in_use = 0;
    this.isNested = false;
    this.object_start = 0;
    this.vtables = [];
    this.vector_num_elems = 0;
    this.force_defaults = false;
    this.string_maps = null;
  }
  /**
   * In order to save space, fields that are set to their default value
   * don't get serialized into the buffer. Forcing defaults provides a
   * way to manually disable this optimization.
   *
   * @param forceDefaults true always serializes default values
   */
  forceDefaults(forceDefaults) {
    this.force_defaults = forceDefaults;
  }
  /**
   * Get the ByteBuffer representing the FlatBuffer. Only call this after you've
   * called finish(). The actual data starts at the ByteBuffer's current position,
   * not necessarily at 0.
   */
  dataBuffer() {
    return this.bb;
  }
  /**
   * Get the bytes representing the FlatBuffer. Only call this after you've
   * called finish().
   */
  asUint8Array() {
    return this.bb.bytes().subarray(this.bb.position(), this.bb.position() + this.offset());
  }
  /**
   * Prepare to write an element of `size` after `additional_bytes` have been
   * written, e.g. if you write a string, you need to align such the int length
   * field is aligned to 4 bytes, and the string data follows it directly. If all
   * you need to do is alignment, `additional_bytes` will be 0.
   *
   * @param size This is the of the new element to write
   * @param additional_bytes The padding size
   */
  prep(size, additional_bytes) {
    if (size > this.minalign) {
      this.minalign = size;
    }
    const align_size = ~(this.bb.capacity() - this.space + additional_bytes) + 1 & size - 1;
    while (this.space < align_size + size + additional_bytes) {
      const old_buf_size = this.bb.capacity();
      this.bb = Builder.growByteBuffer(this.bb);
      this.space += this.bb.capacity() - old_buf_size;
    }
    this.pad(align_size);
  }
  pad(byte_size) {
    for (let i = 0; i < byte_size; i++) {
      this.bb.writeInt8(--this.space, 0);
    }
  }
  writeInt8(value) {
    this.bb.writeInt8(this.space -= 1, value);
  }
  writeInt16(value) {
    this.bb.writeInt16(this.space -= 2, value);
  }
  writeInt32(value) {
    this.bb.writeInt32(this.space -= 4, value);
  }
  writeInt64(value) {
    this.bb.writeInt64(this.space -= 8, value);
  }
  writeFloat32(value) {
    this.bb.writeFloat32(this.space -= 4, value);
  }
  writeFloat64(value) {
    this.bb.writeFloat64(this.space -= 8, value);
  }
  /**
   * Add an `int8` to the buffer, properly aligned, and grows the buffer (if necessary).
   * @param value The `int8` to add the buffer.
   */
  addInt8(value) {
    this.prep(1, 0);
    this.writeInt8(value);
  }
  /**
   * Add an `int16` to the buffer, properly aligned, and grows the buffer (if necessary).
   * @param value The `int16` to add the buffer.
   */
  addInt16(value) {
    this.prep(2, 0);
    this.writeInt16(value);
  }
  /**
   * Add an `int32` to the buffer, properly aligned, and grows the buffer (if necessary).
   * @param value The `int32` to add the buffer.
   */
  addInt32(value) {
    this.prep(4, 0);
    this.writeInt32(value);
  }
  /**
   * Add an `int64` to the buffer, properly aligned, and grows the buffer (if necessary).
   * @param value The `int64` to add the buffer.
   */
  addInt64(value) {
    this.prep(8, 0);
    this.writeInt64(value);
  }
  /**
   * Add a `float32` to the buffer, properly aligned, and grows the buffer (if necessary).
   * @param value The `float32` to add the buffer.
   */
  addFloat32(value) {
    this.prep(4, 0);
    this.writeFloat32(value);
  }
  /**
   * Add a `float64` to the buffer, properly aligned, and grows the buffer (if necessary).
   * @param value The `float64` to add the buffer.
   */
  addFloat64(value) {
    this.prep(8, 0);
    this.writeFloat64(value);
  }
  addFieldInt8(voffset, value, defaultValue) {
    if (this.force_defaults || value != defaultValue) {
      this.addInt8(value);
      this.slot(voffset);
    }
  }
  addFieldInt16(voffset, value, defaultValue) {
    if (this.force_defaults || value != defaultValue) {
      this.addInt16(value);
      this.slot(voffset);
    }
  }
  addFieldInt32(voffset, value, defaultValue) {
    if (this.force_defaults || value != defaultValue) {
      this.addInt32(value);
      this.slot(voffset);
    }
  }
  addFieldInt64(voffset, value, defaultValue) {
    if (this.force_defaults || value !== defaultValue) {
      this.addInt64(value);
      this.slot(voffset);
    }
  }
  addFieldFloat32(voffset, value, defaultValue) {
    if (this.force_defaults || value != defaultValue) {
      this.addFloat32(value);
      this.slot(voffset);
    }
  }
  addFieldFloat64(voffset, value, defaultValue) {
    if (this.force_defaults || value != defaultValue) {
      this.addFloat64(value);
      this.slot(voffset);
    }
  }
  addFieldOffset(voffset, value, defaultValue) {
    if (this.force_defaults || value != defaultValue) {
      this.addOffset(value);
      this.slot(voffset);
    }
  }
  /**
   * Structs are stored inline, so nothing additional is being added. `d` is always 0.
   */
  addFieldStruct(voffset, value, defaultValue) {
    if (value != defaultValue) {
      this.nested(value);
      this.slot(voffset);
    }
  }
  /**
   * Structures are always stored inline, they need to be created right
   * where they're used.  You'll get this assertion failure if you
   * created it elsewhere.
   */
  nested(obj) {
    if (obj != this.offset()) {
      throw new Error("FlatBuffers: struct must be serialized inline.");
    }
  }
  /**
   * Should not be creating any other object, string or vector
   * while an object is being constructed
   */
  notNested() {
    if (this.isNested) {
      throw new Error("FlatBuffers: object serialization must not be nested.");
    }
  }
  /**
   * Set the current vtable at `voffset` to the current location in the buffer.
   */
  slot(voffset) {
    if (this.vtable !== null)
      this.vtable[voffset] = this.offset();
  }
  /**
   * @returns Offset relative to the end of the buffer.
   */
  offset() {
    return this.bb.capacity() - this.space;
  }
  /**
   * Doubles the size of the backing ByteBuffer and copies the old data towards
   * the end of the new buffer (since we build the buffer backwards).
   *
   * @param bb The current buffer with the existing data
   * @returns A new byte buffer with the old data copied
   * to it. The data is located at the end of the buffer.
   *
   * uint8Array.set() formally takes {Array<number>|ArrayBufferView}, so to pass
   * it a uint8Array we need to suppress the type check:
   * @suppress {checkTypes}
   */
  static growByteBuffer(bb) {
    const old_buf_size = bb.capacity();
    if (old_buf_size & 3221225472) {
      throw new Error("FlatBuffers: cannot grow buffer beyond 2 gigabytes.");
    }
    const new_buf_size = old_buf_size << 1;
    const nbb = ByteBuffer.allocate(new_buf_size);
    nbb.setPosition(new_buf_size - old_buf_size);
    nbb.bytes().set(bb.bytes(), new_buf_size - old_buf_size);
    return nbb;
  }
  /**
   * Adds on offset, relative to where it will be written.
   *
   * @param offset The offset to add.
   */
  addOffset(offset) {
    this.prep(SIZEOF_INT, 0);
    this.writeInt32(this.offset() - offset + SIZEOF_INT);
  }
  /**
   * Start encoding a new object in the buffer.  Users will not usually need to
   * call this directly. The FlatBuffers compiler will generate helper methods
   * that call this method internally.
   */
  startObject(numfields) {
    this.notNested();
    if (this.vtable == null) {
      this.vtable = [];
    }
    this.vtable_in_use = numfields;
    for (let i = 0; i < numfields; i++) {
      this.vtable[i] = 0;
    }
    this.isNested = true;
    this.object_start = this.offset();
  }
  /**
   * Finish off writing the object that is under construction.
   *
   * @returns The offset to the object inside `dataBuffer`
   */
  endObject() {
    if (this.vtable == null || !this.isNested) {
      throw new Error("FlatBuffers: endObject called without startObject");
    }
    this.addInt32(0);
    const vtableloc = this.offset();
    let i = this.vtable_in_use - 1;
    for (; i >= 0 && this.vtable[i] == 0; i--) {
    }
    const trimmed_size = i + 1;
    for (; i >= 0; i--) {
      this.addInt16(this.vtable[i] != 0 ? vtableloc - this.vtable[i] : 0);
    }
    const standard_fields = 2;
    this.addInt16(vtableloc - this.object_start);
    const len = (trimmed_size + standard_fields) * SIZEOF_SHORT;
    this.addInt16(len);
    let existing_vtable = 0;
    const vt1 = this.space;
    outer_loop:
      for (i = 0; i < this.vtables.length; i++) {
        const vt2 = this.bb.capacity() - this.vtables[i];
        if (len == this.bb.readInt16(vt2)) {
          for (let j = SIZEOF_SHORT; j < len; j += SIZEOF_SHORT) {
            if (this.bb.readInt16(vt1 + j) != this.bb.readInt16(vt2 + j)) {
              continue outer_loop;
            }
          }
          existing_vtable = this.vtables[i];
          break;
        }
      }
    if (existing_vtable) {
      this.space = this.bb.capacity() - vtableloc;
      this.bb.writeInt32(this.space, existing_vtable - vtableloc);
    } else {
      this.vtables.push(this.offset());
      this.bb.writeInt32(this.bb.capacity() - vtableloc, this.offset() - vtableloc);
    }
    this.isNested = false;
    return vtableloc;
  }
  /**
   * Finalize a buffer, poiting to the given `root_table`.
   */
  finish(root_table, opt_file_identifier, opt_size_prefix) {
    const size_prefix = opt_size_prefix ? SIZE_PREFIX_LENGTH : 0;
    if (opt_file_identifier) {
      const file_identifier = opt_file_identifier;
      this.prep(this.minalign, SIZEOF_INT + FILE_IDENTIFIER_LENGTH + size_prefix);
      if (file_identifier.length != FILE_IDENTIFIER_LENGTH) {
        throw new Error("FlatBuffers: file identifier must be length " + FILE_IDENTIFIER_LENGTH);
      }
      for (let i = FILE_IDENTIFIER_LENGTH - 1; i >= 0; i--) {
        this.writeInt8(file_identifier.charCodeAt(i));
      }
    }
    this.prep(this.minalign, SIZEOF_INT + size_prefix);
    this.addOffset(root_table);
    if (size_prefix) {
      this.addInt32(this.bb.capacity() - this.space);
    }
    this.bb.setPosition(this.space);
  }
  /**
   * Finalize a size prefixed buffer, pointing to the given `root_table`.
   */
  finishSizePrefixed(root_table, opt_file_identifier) {
    this.finish(root_table, opt_file_identifier, true);
  }
  /**
   * This checks a required field has been set in a given table that has
   * just been constructed.
   */
  requiredField(table, field) {
    const table_start = this.bb.capacity() - table;
    const vtable_start = table_start - this.bb.readInt32(table_start);
    const ok = field < this.bb.readInt16(vtable_start) && this.bb.readInt16(vtable_start + field) != 0;
    if (!ok) {
      throw new Error("FlatBuffers: field " + field + " must be set");
    }
  }
  /**
   * Start a new array/vector of objects.  Users usually will not call
   * this directly. The FlatBuffers compiler will create a start/end
   * method for vector types in generated code.
   *
   * @param elem_size The size of each element in the array
   * @param num_elems The number of elements in the array
   * @param alignment The alignment of the array
   */
  startVector(elem_size, num_elems, alignment) {
    this.notNested();
    this.vector_num_elems = num_elems;
    this.prep(SIZEOF_INT, elem_size * num_elems);
    this.prep(alignment, elem_size * num_elems);
  }
  /**
   * Finish off the creation of an array and all its elements. The array must be
   * created with `startVector`.
   *
   * @returns The offset at which the newly created array
   * starts.
   */
  endVector() {
    this.writeInt32(this.vector_num_elems);
    return this.offset();
  }
  /**
   * Encode the string `s` in the buffer using UTF-8. If the string passed has
   * already been seen, we return the offset of the already written string
   *
   * @param s The string to encode
   * @return The offset in the buffer where the encoded string starts
   */
  createSharedString(s) {
    if (!s) {
      return 0;
    }
    if (!this.string_maps) {
      this.string_maps = /* @__PURE__ */ new Map();
    }
    if (this.string_maps.has(s)) {
      return this.string_maps.get(s);
    }
    const offset = this.createString(s);
    this.string_maps.set(s, offset);
    return offset;
  }
  /**
   * Encode the string `s` in the buffer using UTF-8. If a Uint8Array is passed
   * instead of a string, it is assumed to contain valid UTF-8 encoded data.
   *
   * @param s The string to encode
   * @return The offset in the buffer where the encoded string starts
   */
  createString(s) {
    if (s === null || s === void 0) {
      return 0;
    }
    let utf8;
    if (s instanceof Uint8Array) {
      utf8 = s;
    } else {
      utf8 = this.text_encoder.encode(s);
    }
    this.addInt8(0);
    this.startVector(1, utf8.length, 1);
    this.bb.setPosition(this.space -= utf8.length);
    for (let i = 0, offset = this.space, bytes = this.bb.bytes(); i < utf8.length; i++) {
      bytes[offset++] = utf8[i];
    }
    return this.endVector();
  }
  /**
   * A helper function to pack an object
   *
   * @returns offset of obj
   */
  createObjectOffset(obj) {
    if (obj === null) {
      return 0;
    }
    if (typeof obj === "string") {
      return this.createString(obj);
    } else {
      return obj.pack(this);
    }
  }
  /**
   * A helper function to pack a list of object
   *
   * @returns list of offsets of each non null object
   */
  createObjectOffsetList(list) {
    const ret = [];
    for (let i = 0; i < list.length; ++i) {
      const val = list[i];
      if (val !== null) {
        ret.push(this.createObjectOffset(val));
      } else {
        throw new Error("FlatBuffers: Argument for createObjectOffsetList cannot contain null.");
      }
    }
    return ret;
  }
  createStructOffsetList(list, startFunc) {
    startFunc(this, list.length);
    this.createObjectOffsetList(list.slice().reverse());
    return this.endVector();
  }
}
let CivilCurve$2 = class CivilCurve {
  constructor() {
    __publicField(this, "bb", null);
    __publicField(this, "bb_pos", 0);
  }
  __init(i, bb) {
    this.bb_pos = i;
    this.bb = bb;
    return this;
  }
  static getRootAsCivilCurve(bb, obj) {
    return (obj || new CivilCurve()).__init(
      bb.readInt32(bb.position()) + bb.position(),
      bb
    );
  }
  static getSizePrefixedRootAsCivilCurve(bb, obj) {
    bb.setPosition(bb.position() + SIZE_PREFIX_LENGTH);
    return (obj || new CivilCurve()).__init(
      bb.readInt32(bb.position()) + bb.position(),
      bb
    );
  }
  points(index) {
    const offset = this.bb.__offset(this.bb_pos, 4);
    return offset ? this.bb.readFloat32(
      this.bb.__vector(this.bb_pos + offset) + index * 4
    ) : 0;
  }
  pointsLength() {
    const offset = this.bb.__offset(this.bb_pos, 4);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  pointsArray() {
    const offset = this.bb.__offset(this.bb_pos, 4);
    return offset ? new Float32Array(
      this.bb.bytes().buffer,
      this.bb.bytes().byteOffset + this.bb.__vector(this.bb_pos + offset),
      this.bb.__vector_len(this.bb_pos + offset)
    ) : null;
  }
  data(optionalEncoding) {
    const offset = this.bb.__offset(this.bb_pos, 6);
    return offset ? this.bb.__string(this.bb_pos + offset, optionalEncoding) : null;
  }
  static startCivilCurve(builder) {
    builder.startObject(2);
  }
  static addPoints(builder, pointsOffset) {
    builder.addFieldOffset(0, pointsOffset, 0);
  }
  static createPointsVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addFloat32(data[i]);
    }
    return builder.endVector();
  }
  static startPointsVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static addData(builder, dataOffset) {
    builder.addFieldOffset(1, dataOffset, 0);
  }
  static endCivilCurve(builder) {
    const offset = builder.endObject();
    return offset;
  }
  static createCivilCurve(builder, pointsOffset, dataOffset) {
    CivilCurve.startCivilCurve(builder);
    CivilCurve.addPoints(builder, pointsOffset);
    CivilCurve.addData(builder, dataOffset);
    return CivilCurve.endCivilCurve(builder);
  }
};
let Alignment$2 = class Alignment {
  constructor() {
    __publicField(this, "bb", null);
    __publicField(this, "bb_pos", 0);
  }
  __init(i, bb) {
    this.bb_pos = i;
    this.bb = bb;
    return this;
  }
  static getRootAsAlignment(bb, obj) {
    return (obj || new Alignment()).__init(
      bb.readInt32(bb.position()) + bb.position(),
      bb
    );
  }
  static getSizePrefixedRootAsAlignment(bb, obj) {
    bb.setPosition(bb.position() + SIZE_PREFIX_LENGTH);
    return (obj || new Alignment()).__init(
      bb.readInt32(bb.position()) + bb.position(),
      bb
    );
  }
  vertical(index, obj) {
    const offset = this.bb.__offset(this.bb_pos, 4);
    return offset ? (obj || new CivilCurve$2()).__init(
      this.bb.__indirect(
        this.bb.__vector(this.bb_pos + offset) + index * 4
      ),
      this.bb
    ) : null;
  }
  verticalLength() {
    const offset = this.bb.__offset(this.bb_pos, 4);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  horizontal(index, obj) {
    const offset = this.bb.__offset(this.bb_pos, 6);
    return offset ? (obj || new CivilCurve$2()).__init(
      this.bb.__indirect(
        this.bb.__vector(this.bb_pos + offset) + index * 4
      ),
      this.bb
    ) : null;
  }
  horizontalLength() {
    const offset = this.bb.__offset(this.bb_pos, 6);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  absolute(index, obj) {
    const offset = this.bb.__offset(this.bb_pos, 8);
    return offset ? (obj || new CivilCurve$2()).__init(
      this.bb.__indirect(
        this.bb.__vector(this.bb_pos + offset) + index * 4
      ),
      this.bb
    ) : null;
  }
  absoluteLength() {
    const offset = this.bb.__offset(this.bb_pos, 8);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  initialPk() {
    const offset = this.bb.__offset(this.bb_pos, 10);
    return offset ? this.bb.readFloat32(this.bb_pos + offset) : 0;
  }
  static startAlignment(builder) {
    builder.startObject(4);
  }
  static addVertical(builder, verticalOffset) {
    builder.addFieldOffset(0, verticalOffset, 0);
  }
  static createVerticalVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addOffset(data[i]);
    }
    return builder.endVector();
  }
  static startVerticalVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static addHorizontal(builder, horizontalOffset) {
    builder.addFieldOffset(1, horizontalOffset, 0);
  }
  static createHorizontalVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addOffset(data[i]);
    }
    return builder.endVector();
  }
  static startHorizontalVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static addAbsolute(builder, absoluteOffset) {
    builder.addFieldOffset(2, absoluteOffset, 0);
  }
  static createAbsoluteVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addOffset(data[i]);
    }
    return builder.endVector();
  }
  static startAbsoluteVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static addInitialPk(builder, initialPk) {
    builder.addFieldFloat32(3, initialPk, 0);
  }
  static endAlignment(builder) {
    const offset = builder.endObject();
    return offset;
  }
  static createAlignment(builder, verticalOffset, horizontalOffset, absoluteOffset, initialPk) {
    Alignment.startAlignment(builder);
    Alignment.addVertical(builder, verticalOffset);
    Alignment.addHorizontal(builder, horizontalOffset);
    Alignment.addAbsolute(builder, absoluteOffset);
    Alignment.addInitialPk(builder, initialPk);
    return Alignment.endAlignment(builder);
  }
};
let CivilData$1 = class CivilData {
  constructor() {
    __publicField(this, "bb", null);
    __publicField(this, "bb_pos", 0);
  }
  __init(i, bb) {
    this.bb_pos = i;
    this.bb = bb;
    return this;
  }
  static getRootAsCivilData(bb, obj) {
    return (obj || new CivilData()).__init(
      bb.readInt32(bb.position()) + bb.position(),
      bb
    );
  }
  static getSizePrefixedRootAsCivilData(bb, obj) {
    bb.setPosition(bb.position() + SIZE_PREFIX_LENGTH);
    return (obj || new CivilData()).__init(
      bb.readInt32(bb.position()) + bb.position(),
      bb
    );
  }
  alignments(index, obj) {
    const offset = this.bb.__offset(this.bb_pos, 4);
    return offset ? (obj || new Alignment$2()).__init(
      this.bb.__indirect(
        this.bb.__vector(this.bb_pos + offset) + index * 4
      ),
      this.bb
    ) : null;
  }
  alignmentsLength() {
    const offset = this.bb.__offset(this.bb_pos, 4);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  coordinationMatrix(index) {
    const offset = this.bb.__offset(this.bb_pos, 6);
    return offset ? this.bb.readFloat32(
      this.bb.__vector(this.bb_pos + offset) + index * 4
    ) : 0;
  }
  coordinationMatrixLength() {
    const offset = this.bb.__offset(this.bb_pos, 6);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  coordinationMatrixArray() {
    const offset = this.bb.__offset(this.bb_pos, 6);
    return offset ? new Float32Array(
      this.bb.bytes().buffer,
      this.bb.bytes().byteOffset + this.bb.__vector(this.bb_pos + offset),
      this.bb.__vector_len(this.bb_pos + offset)
    ) : null;
  }
  static startCivilData(builder) {
    builder.startObject(2);
  }
  static addAlignments(builder, alignmentsOffset) {
    builder.addFieldOffset(0, alignmentsOffset, 0);
  }
  static createAlignmentsVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addOffset(data[i]);
    }
    return builder.endVector();
  }
  static startAlignmentsVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static addCoordinationMatrix(builder, coordinationMatrixOffset) {
    builder.addFieldOffset(1, coordinationMatrixOffset, 0);
  }
  static createCoordinationMatrixVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addFloat32(data[i]);
    }
    return builder.endVector();
  }
  static startCoordinationMatrixVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static endCivilData(builder) {
    const offset = builder.endObject();
    return offset;
  }
  static createCivilData(builder, alignmentsOffset, coordinationMatrixOffset) {
    CivilData.startCivilData(builder);
    CivilData.addAlignments(builder, alignmentsOffset);
    CivilData.addCoordinationMatrix(builder, coordinationMatrixOffset);
    return CivilData.endCivilData(builder);
  }
};
let Fragment$1 = class Fragment2 {
  constructor() {
    __publicField(this, "bb", null);
    __publicField(this, "bb_pos", 0);
  }
  __init(i, bb) {
    this.bb_pos = i;
    this.bb = bb;
    return this;
  }
  static getRootAsFragment(bb, obj) {
    return (obj || new Fragment2()).__init(bb.readInt32(bb.position()) + bb.position(), bb);
  }
  static getSizePrefixedRootAsFragment(bb, obj) {
    bb.setPosition(bb.position() + SIZE_PREFIX_LENGTH);
    return (obj || new Fragment2()).__init(bb.readInt32(bb.position()) + bb.position(), bb);
  }
  position(index) {
    const offset = this.bb.__offset(this.bb_pos, 4);
    return offset ? this.bb.readFloat32(this.bb.__vector(this.bb_pos + offset) + index * 4) : 0;
  }
  positionLength() {
    const offset = this.bb.__offset(this.bb_pos, 4);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  positionArray() {
    const offset = this.bb.__offset(this.bb_pos, 4);
    return offset ? new Float32Array(this.bb.bytes().buffer, this.bb.bytes().byteOffset + this.bb.__vector(this.bb_pos + offset), this.bb.__vector_len(this.bb_pos + offset)) : null;
  }
  normal(index) {
    const offset = this.bb.__offset(this.bb_pos, 6);
    return offset ? this.bb.readFloat32(this.bb.__vector(this.bb_pos + offset) + index * 4) : 0;
  }
  normalLength() {
    const offset = this.bb.__offset(this.bb_pos, 6);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  normalArray() {
    const offset = this.bb.__offset(this.bb_pos, 6);
    return offset ? new Float32Array(this.bb.bytes().buffer, this.bb.bytes().byteOffset + this.bb.__vector(this.bb_pos + offset), this.bb.__vector_len(this.bb_pos + offset)) : null;
  }
  index(index) {
    const offset = this.bb.__offset(this.bb_pos, 8);
    return offset ? this.bb.readUint32(this.bb.__vector(this.bb_pos + offset) + index * 4) : 0;
  }
  indexLength() {
    const offset = this.bb.__offset(this.bb_pos, 8);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  indexArray() {
    const offset = this.bb.__offset(this.bb_pos, 8);
    return offset ? new Uint32Array(this.bb.bytes().buffer, this.bb.bytes().byteOffset + this.bb.__vector(this.bb_pos + offset), this.bb.__vector_len(this.bb_pos + offset)) : null;
  }
  groups(index) {
    const offset = this.bb.__offset(this.bb_pos, 10);
    return offset ? this.bb.readFloat32(this.bb.__vector(this.bb_pos + offset) + index * 4) : 0;
  }
  groupsLength() {
    const offset = this.bb.__offset(this.bb_pos, 10);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  groupsArray() {
    const offset = this.bb.__offset(this.bb_pos, 10);
    return offset ? new Float32Array(this.bb.bytes().buffer, this.bb.bytes().byteOffset + this.bb.__vector(this.bb_pos + offset), this.bb.__vector_len(this.bb_pos + offset)) : null;
  }
  materials(index) {
    const offset = this.bb.__offset(this.bb_pos, 12);
    return offset ? this.bb.readFloat32(this.bb.__vector(this.bb_pos + offset) + index * 4) : 0;
  }
  materialsLength() {
    const offset = this.bb.__offset(this.bb_pos, 12);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  materialsArray() {
    const offset = this.bb.__offset(this.bb_pos, 12);
    return offset ? new Float32Array(this.bb.bytes().buffer, this.bb.bytes().byteOffset + this.bb.__vector(this.bb_pos + offset), this.bb.__vector_len(this.bb_pos + offset)) : null;
  }
  matrices(index) {
    const offset = this.bb.__offset(this.bb_pos, 14);
    return offset ? this.bb.readFloat32(this.bb.__vector(this.bb_pos + offset) + index * 4) : 0;
  }
  matricesLength() {
    const offset = this.bb.__offset(this.bb_pos, 14);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  matricesArray() {
    const offset = this.bb.__offset(this.bb_pos, 14);
    return offset ? new Float32Array(this.bb.bytes().buffer, this.bb.bytes().byteOffset + this.bb.__vector(this.bb_pos + offset), this.bb.__vector_len(this.bb_pos + offset)) : null;
  }
  colors(index) {
    const offset = this.bb.__offset(this.bb_pos, 16);
    return offset ? this.bb.readFloat32(this.bb.__vector(this.bb_pos + offset) + index * 4) : 0;
  }
  colorsLength() {
    const offset = this.bb.__offset(this.bb_pos, 16);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  colorsArray() {
    const offset = this.bb.__offset(this.bb_pos, 16);
    return offset ? new Float32Array(this.bb.bytes().buffer, this.bb.bytes().byteOffset + this.bb.__vector(this.bb_pos + offset), this.bb.__vector_len(this.bb_pos + offset)) : null;
  }
  itemsSize(index) {
    const offset = this.bb.__offset(this.bb_pos, 18);
    return offset ? this.bb.readUint32(this.bb.__vector(this.bb_pos + offset) + index * 4) : 0;
  }
  itemsSizeLength() {
    const offset = this.bb.__offset(this.bb_pos, 18);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  itemsSizeArray() {
    const offset = this.bb.__offset(this.bb_pos, 18);
    return offset ? new Uint32Array(this.bb.bytes().buffer, this.bb.bytes().byteOffset + this.bb.__vector(this.bb_pos + offset), this.bb.__vector_len(this.bb_pos + offset)) : null;
  }
  ids(index) {
    const offset = this.bb.__offset(this.bb_pos, 20);
    return offset ? this.bb.readUint32(this.bb.__vector(this.bb_pos + offset) + index * 4) : 0;
  }
  idsLength() {
    const offset = this.bb.__offset(this.bb_pos, 20);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  idsArray() {
    const offset = this.bb.__offset(this.bb_pos, 20);
    return offset ? new Uint32Array(this.bb.bytes().buffer, this.bb.bytes().byteOffset + this.bb.__vector(this.bb_pos + offset), this.bb.__vector_len(this.bb_pos + offset)) : null;
  }
  id(optionalEncoding) {
    const offset = this.bb.__offset(this.bb_pos, 22);
    return offset ? this.bb.__string(this.bb_pos + offset, optionalEncoding) : null;
  }
  capacity() {
    const offset = this.bb.__offset(this.bb_pos, 24);
    return offset ? this.bb.readUint32(this.bb_pos + offset) : 0;
  }
  capacityOffset() {
    const offset = this.bb.__offset(this.bb_pos, 26);
    return offset ? this.bb.readUint32(this.bb_pos + offset) : 0;
  }
  static startFragment(builder) {
    builder.startObject(12);
  }
  static addPosition(builder, positionOffset) {
    builder.addFieldOffset(0, positionOffset, 0);
  }
  static createPositionVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addFloat32(data[i]);
    }
    return builder.endVector();
  }
  static startPositionVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static addNormal(builder, normalOffset) {
    builder.addFieldOffset(1, normalOffset, 0);
  }
  static createNormalVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addFloat32(data[i]);
    }
    return builder.endVector();
  }
  static startNormalVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static addIndex(builder, indexOffset) {
    builder.addFieldOffset(2, indexOffset, 0);
  }
  static createIndexVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addInt32(data[i]);
    }
    return builder.endVector();
  }
  static startIndexVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static addGroups(builder, groupsOffset) {
    builder.addFieldOffset(3, groupsOffset, 0);
  }
  static createGroupsVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addFloat32(data[i]);
    }
    return builder.endVector();
  }
  static startGroupsVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static addMaterials(builder, materialsOffset) {
    builder.addFieldOffset(4, materialsOffset, 0);
  }
  static createMaterialsVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addFloat32(data[i]);
    }
    return builder.endVector();
  }
  static startMaterialsVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static addMatrices(builder, matricesOffset) {
    builder.addFieldOffset(5, matricesOffset, 0);
  }
  static createMatricesVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addFloat32(data[i]);
    }
    return builder.endVector();
  }
  static startMatricesVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static addColors(builder, colorsOffset) {
    builder.addFieldOffset(6, colorsOffset, 0);
  }
  static createColorsVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addFloat32(data[i]);
    }
    return builder.endVector();
  }
  static startColorsVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static addItemsSize(builder, itemsSizeOffset) {
    builder.addFieldOffset(7, itemsSizeOffset, 0);
  }
  static createItemsSizeVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addInt32(data[i]);
    }
    return builder.endVector();
  }
  static startItemsSizeVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static addIds(builder, idsOffset) {
    builder.addFieldOffset(8, idsOffset, 0);
  }
  static createIdsVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addInt32(data[i]);
    }
    return builder.endVector();
  }
  static startIdsVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static addId(builder, idOffset) {
    builder.addFieldOffset(9, idOffset, 0);
  }
  static addCapacity(builder, capacity) {
    builder.addFieldInt32(10, capacity, 0);
  }
  static addCapacityOffset(builder, capacityOffset) {
    builder.addFieldInt32(11, capacityOffset, 0);
  }
  static endFragment(builder) {
    const offset = builder.endObject();
    return offset;
  }
  static createFragment(builder, positionOffset, normalOffset, indexOffset, groupsOffset, materialsOffset, matricesOffset, colorsOffset, itemsSizeOffset, idsOffset, idOffset, capacity, capacityOffset) {
    Fragment2.startFragment(builder);
    Fragment2.addPosition(builder, positionOffset);
    Fragment2.addNormal(builder, normalOffset);
    Fragment2.addIndex(builder, indexOffset);
    Fragment2.addGroups(builder, groupsOffset);
    Fragment2.addMaterials(builder, materialsOffset);
    Fragment2.addMatrices(builder, matricesOffset);
    Fragment2.addColors(builder, colorsOffset);
    Fragment2.addItemsSize(builder, itemsSizeOffset);
    Fragment2.addIds(builder, idsOffset);
    Fragment2.addId(builder, idOffset);
    Fragment2.addCapacity(builder, capacity);
    Fragment2.addCapacityOffset(builder, capacityOffset);
    return Fragment2.endFragment(builder);
  }
};
let FragmentsGroup$2 = class FragmentsGroup {
  constructor() {
    __publicField(this, "bb", null);
    __publicField(this, "bb_pos", 0);
  }
  __init(i, bb) {
    this.bb_pos = i;
    this.bb = bb;
    return this;
  }
  static getRootAsFragmentsGroup(bb, obj) {
    return (obj || new FragmentsGroup()).__init(
      bb.readInt32(bb.position()) + bb.position(),
      bb
    );
  }
  static getSizePrefixedRootAsFragmentsGroup(bb, obj) {
    bb.setPosition(bb.position() + SIZE_PREFIX_LENGTH);
    return (obj || new FragmentsGroup()).__init(
      bb.readInt32(bb.position()) + bb.position(),
      bb
    );
  }
  items(index, obj) {
    const offset = this.bb.__offset(this.bb_pos, 4);
    return offset ? (obj || new Fragment$1()).__init(
      this.bb.__indirect(
        this.bb.__vector(this.bb_pos + offset) + index * 4
      ),
      this.bb
    ) : null;
  }
  itemsLength() {
    const offset = this.bb.__offset(this.bb_pos, 4);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  civil(obj) {
    const offset = this.bb.__offset(this.bb_pos, 6);
    return offset ? (obj || new CivilData$1()).__init(
      this.bb.__indirect(this.bb_pos + offset),
      this.bb
    ) : null;
  }
  coordinationMatrix(index) {
    const offset = this.bb.__offset(this.bb_pos, 8);
    return offset ? this.bb.readFloat32(
      this.bb.__vector(this.bb_pos + offset) + index * 4
    ) : 0;
  }
  coordinationMatrixLength() {
    const offset = this.bb.__offset(this.bb_pos, 8);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  coordinationMatrixArray() {
    const offset = this.bb.__offset(this.bb_pos, 8);
    return offset ? new Float32Array(
      this.bb.bytes().buffer,
      this.bb.bytes().byteOffset + this.bb.__vector(this.bb_pos + offset),
      this.bb.__vector_len(this.bb_pos + offset)
    ) : null;
  }
  ids(index) {
    const offset = this.bb.__offset(this.bb_pos, 10);
    return offset ? this.bb.readUint32(this.bb.__vector(this.bb_pos + offset) + index * 4) : 0;
  }
  idsLength() {
    const offset = this.bb.__offset(this.bb_pos, 10);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  idsArray() {
    const offset = this.bb.__offset(this.bb_pos, 10);
    return offset ? new Uint32Array(
      this.bb.bytes().buffer,
      this.bb.bytes().byteOffset + this.bb.__vector(this.bb_pos + offset),
      this.bb.__vector_len(this.bb_pos + offset)
    ) : null;
  }
  itemsKeys(index) {
    const offset = this.bb.__offset(this.bb_pos, 12);
    return offset ? this.bb.readUint32(this.bb.__vector(this.bb_pos + offset) + index * 4) : 0;
  }
  itemsKeysLength() {
    const offset = this.bb.__offset(this.bb_pos, 12);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  itemsKeysArray() {
    const offset = this.bb.__offset(this.bb_pos, 12);
    return offset ? new Uint32Array(
      this.bb.bytes().buffer,
      this.bb.bytes().byteOffset + this.bb.__vector(this.bb_pos + offset),
      this.bb.__vector_len(this.bb_pos + offset)
    ) : null;
  }
  itemsKeysIndices(index) {
    const offset = this.bb.__offset(this.bb_pos, 14);
    return offset ? this.bb.readUint32(this.bb.__vector(this.bb_pos + offset) + index * 4) : 0;
  }
  itemsKeysIndicesLength() {
    const offset = this.bb.__offset(this.bb_pos, 14);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  itemsKeysIndicesArray() {
    const offset = this.bb.__offset(this.bb_pos, 14);
    return offset ? new Uint32Array(
      this.bb.bytes().buffer,
      this.bb.bytes().byteOffset + this.bb.__vector(this.bb_pos + offset),
      this.bb.__vector_len(this.bb_pos + offset)
    ) : null;
  }
  itemsRels(index) {
    const offset = this.bb.__offset(this.bb_pos, 16);
    return offset ? this.bb.readUint32(this.bb.__vector(this.bb_pos + offset) + index * 4) : 0;
  }
  itemsRelsLength() {
    const offset = this.bb.__offset(this.bb_pos, 16);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  itemsRelsArray() {
    const offset = this.bb.__offset(this.bb_pos, 16);
    return offset ? new Uint32Array(
      this.bb.bytes().buffer,
      this.bb.bytes().byteOffset + this.bb.__vector(this.bb_pos + offset),
      this.bb.__vector_len(this.bb_pos + offset)
    ) : null;
  }
  itemsRelsIndices(index) {
    const offset = this.bb.__offset(this.bb_pos, 18);
    return offset ? this.bb.readUint32(this.bb.__vector(this.bb_pos + offset) + index * 4) : 0;
  }
  itemsRelsIndicesLength() {
    const offset = this.bb.__offset(this.bb_pos, 18);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  itemsRelsIndicesArray() {
    const offset = this.bb.__offset(this.bb_pos, 18);
    return offset ? new Uint32Array(
      this.bb.bytes().buffer,
      this.bb.bytes().byteOffset + this.bb.__vector(this.bb_pos + offset),
      this.bb.__vector_len(this.bb_pos + offset)
    ) : null;
  }
  fragmentKeys(optionalEncoding) {
    const offset = this.bb.__offset(this.bb_pos, 20);
    return offset ? this.bb.__string(this.bb_pos + offset, optionalEncoding) : null;
  }
  id(optionalEncoding) {
    const offset = this.bb.__offset(this.bb_pos, 22);
    return offset ? this.bb.__string(this.bb_pos + offset, optionalEncoding) : null;
  }
  name(optionalEncoding) {
    const offset = this.bb.__offset(this.bb_pos, 24);
    return offset ? this.bb.__string(this.bb_pos + offset, optionalEncoding) : null;
  }
  ifcName(optionalEncoding) {
    const offset = this.bb.__offset(this.bb_pos, 26);
    return offset ? this.bb.__string(this.bb_pos + offset, optionalEncoding) : null;
  }
  ifcDescription(optionalEncoding) {
    const offset = this.bb.__offset(this.bb_pos, 28);
    return offset ? this.bb.__string(this.bb_pos + offset, optionalEncoding) : null;
  }
  ifcSchema(optionalEncoding) {
    const offset = this.bb.__offset(this.bb_pos, 30);
    return offset ? this.bb.__string(this.bb_pos + offset, optionalEncoding) : null;
  }
  maxExpressId() {
    const offset = this.bb.__offset(this.bb_pos, 32);
    return offset ? this.bb.readUint32(this.bb_pos + offset) : 0;
  }
  boundingBox(index) {
    const offset = this.bb.__offset(this.bb_pos, 34);
    return offset ? this.bb.readFloat32(
      this.bb.__vector(this.bb_pos + offset) + index * 4
    ) : 0;
  }
  boundingBoxLength() {
    const offset = this.bb.__offset(this.bb_pos, 34);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  boundingBoxArray() {
    const offset = this.bb.__offset(this.bb_pos, 34);
    return offset ? new Float32Array(
      this.bb.bytes().buffer,
      this.bb.bytes().byteOffset + this.bb.__vector(this.bb_pos + offset),
      this.bb.__vector_len(this.bb_pos + offset)
    ) : null;
  }
  opaqueGeometriesIds(index) {
    const offset = this.bb.__offset(this.bb_pos, 36);
    return offset ? this.bb.readInt32(this.bb.__vector(this.bb_pos + offset) + index * 4) : 0;
  }
  opaqueGeometriesIdsLength() {
    const offset = this.bb.__offset(this.bb_pos, 36);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  opaqueGeometriesIdsArray() {
    const offset = this.bb.__offset(this.bb_pos, 36);
    return offset ? new Int32Array(
      this.bb.bytes().buffer,
      this.bb.bytes().byteOffset + this.bb.__vector(this.bb_pos + offset),
      this.bb.__vector_len(this.bb_pos + offset)
    ) : null;
  }
  transparentGeometriesIds(index) {
    const offset = this.bb.__offset(this.bb_pos, 38);
    return offset ? this.bb.readInt32(this.bb.__vector(this.bb_pos + offset) + index * 4) : 0;
  }
  transparentGeometriesIdsLength() {
    const offset = this.bb.__offset(this.bb_pos, 38);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  transparentGeometriesIdsArray() {
    const offset = this.bb.__offset(this.bb_pos, 38);
    return offset ? new Int32Array(
      this.bb.bytes().buffer,
      this.bb.bytes().byteOffset + this.bb.__vector(this.bb_pos + offset),
      this.bb.__vector_len(this.bb_pos + offset)
    ) : null;
  }
  static startFragmentsGroup(builder) {
    builder.startObject(18);
  }
  static addItems(builder, itemsOffset) {
    builder.addFieldOffset(0, itemsOffset, 0);
  }
  static createItemsVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addOffset(data[i]);
    }
    return builder.endVector();
  }
  static startItemsVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static addCivil(builder, civilOffset) {
    builder.addFieldOffset(1, civilOffset, 0);
  }
  static addCoordinationMatrix(builder, coordinationMatrixOffset) {
    builder.addFieldOffset(2, coordinationMatrixOffset, 0);
  }
  static createCoordinationMatrixVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addFloat32(data[i]);
    }
    return builder.endVector();
  }
  static startCoordinationMatrixVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static addIds(builder, idsOffset) {
    builder.addFieldOffset(3, idsOffset, 0);
  }
  static createIdsVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addInt32(data[i]);
    }
    return builder.endVector();
  }
  static startIdsVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static addItemsKeys(builder, itemsKeysOffset) {
    builder.addFieldOffset(4, itemsKeysOffset, 0);
  }
  static createItemsKeysVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addInt32(data[i]);
    }
    return builder.endVector();
  }
  static startItemsKeysVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static addItemsKeysIndices(builder, itemsKeysIndicesOffset) {
    builder.addFieldOffset(5, itemsKeysIndicesOffset, 0);
  }
  static createItemsKeysIndicesVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addInt32(data[i]);
    }
    return builder.endVector();
  }
  static startItemsKeysIndicesVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static addItemsRels(builder, itemsRelsOffset) {
    builder.addFieldOffset(6, itemsRelsOffset, 0);
  }
  static createItemsRelsVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addInt32(data[i]);
    }
    return builder.endVector();
  }
  static startItemsRelsVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static addItemsRelsIndices(builder, itemsRelsIndicesOffset) {
    builder.addFieldOffset(7, itemsRelsIndicesOffset, 0);
  }
  static createItemsRelsIndicesVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addInt32(data[i]);
    }
    return builder.endVector();
  }
  static startItemsRelsIndicesVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static addFragmentKeys(builder, fragmentKeysOffset) {
    builder.addFieldOffset(8, fragmentKeysOffset, 0);
  }
  static addId(builder, idOffset) {
    builder.addFieldOffset(9, idOffset, 0);
  }
  static addName(builder, nameOffset) {
    builder.addFieldOffset(10, nameOffset, 0);
  }
  static addIfcName(builder, ifcNameOffset) {
    builder.addFieldOffset(11, ifcNameOffset, 0);
  }
  static addIfcDescription(builder, ifcDescriptionOffset) {
    builder.addFieldOffset(12, ifcDescriptionOffset, 0);
  }
  static addIfcSchema(builder, ifcSchemaOffset) {
    builder.addFieldOffset(13, ifcSchemaOffset, 0);
  }
  static addMaxExpressId(builder, maxExpressId) {
    builder.addFieldInt32(14, maxExpressId, 0);
  }
  static addBoundingBox(builder, boundingBoxOffset) {
    builder.addFieldOffset(15, boundingBoxOffset, 0);
  }
  static createBoundingBoxVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addFloat32(data[i]);
    }
    return builder.endVector();
  }
  static startBoundingBoxVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static addOpaqueGeometriesIds(builder, opaqueGeometriesIdsOffset) {
    builder.addFieldOffset(16, opaqueGeometriesIdsOffset, 0);
  }
  static createOpaqueGeometriesIdsVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addInt32(data[i]);
    }
    return builder.endVector();
  }
  static startOpaqueGeometriesIdsVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static addTransparentGeometriesIds(builder, transparentGeometriesIdsOffset) {
    builder.addFieldOffset(17, transparentGeometriesIdsOffset, 0);
  }
  static createTransparentGeometriesIdsVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addInt32(data[i]);
    }
    return builder.endVector();
  }
  static startTransparentGeometriesIdsVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static endFragmentsGroup(builder) {
    const offset = builder.endObject();
    return offset;
  }
  static finishFragmentsGroupBuffer(builder, offset) {
    builder.finish(offset);
  }
  static finishSizePrefixedFragmentsGroupBuffer(builder, offset) {
    builder.finish(offset, void 0, true);
  }
};
class ParserV1 {
  constructor() {
    __publicField(this, "version", 1);
    __publicField(this, "fragmentIDSeparator", "|");
  }
  /** {@link FragmentParser.import} */
  import(bytes) {
    const buffer = new ByteBuffer(bytes);
    const fbFragmentsGroup = FragmentsGroup$2.getRootAsFragmentsGroup(buffer);
    const fragmentsGroup = this.constructFragmentGroup(fbFragmentsGroup);
    const length = fbFragmentsGroup.itemsLength();
    for (let i = 0; i < length; i++) {
      const fbFragment = fbFragmentsGroup.items(i);
      if (!fbFragment)
        continue;
      const geometry = this.constructGeometry(fbFragment);
      const materials = this.constructMaterials(fbFragment);
      const capacity = fbFragment.capacity();
      const fragment = new Fragment$2(geometry, materials, capacity);
      fragment.capacityOffset = fbFragment.capacityOffset();
      this.setInstances(fbFragment, fragment);
      this.setID(fbFragment, fragment);
      fragmentsGroup.items.push(fragment);
      fragmentsGroup.add(fragment.mesh);
    }
    return fragmentsGroup;
  }
  /**
   * Exports the FragmentsGroup to a flatbuffer binary file.
   *
   * @param group - The FragmentsGroup to be exported.
   * @returns The flatbuffer binary file as a Uint8Array.
   */
  export(group) {
    const builder = new Builder(1024);
    const items = [];
    const G = FragmentsGroup$2;
    const F = Fragment$1;
    let civilData = null;
    if (group.civilData) {
      const alignments = [];
      const A = Alignment$2;
      const C = CivilData$1;
      for (const [_id, alignment] of group.civilData.alignments) {
        const { absolute, horizontal, vertical } = alignment;
        const horCurves = this.saveCivilCurves(horizontal, builder);
        const verCurves = this.saveCivilCurves(vertical, builder);
        const absCurves = this.saveCivilCurves(absolute, builder);
        const horVector = A.createHorizontalVector(builder, horCurves);
        const verVector = A.createVerticalVector(builder, verCurves);
        const absVector = A.createAbsoluteVector(builder, absCurves);
        A.startAlignment(builder);
        A.addHorizontal(builder, horVector);
        A.addVertical(builder, verVector);
        A.addAbsolute(builder, absVector);
        A.addInitialPk(builder, alignment.initialKP);
        const exported = A.endAlignment(builder);
        alignments.push(exported);
      }
      const algVector = C.createAlignmentsVector(builder, alignments);
      const coordVector = C.createCoordinationMatrixVector(
        builder,
        group.coordinationMatrix.elements
      );
      C.startCivilData(builder);
      C.addAlignments(builder, algVector);
      C.addCoordinationMatrix(builder, coordVector);
      civilData = C.endCivilData(builder);
    }
    for (const fragment of group.items) {
      const result2 = fragment.exportData();
      const itemsSize = [];
      for (const itemID of fragment.ids) {
        const instances = fragment.getInstancesIDs(itemID);
        if (!instances) {
          throw new Error("Instances not found!");
        }
        itemsSize.push(instances.size);
      }
      const posVector = F.createPositionVector(builder, result2.position);
      const normalVector = F.createNormalVector(builder, result2.normal);
      const indexVector = F.createIndexVector(builder, result2.index);
      const groupsVector = F.createGroupsVector(builder, result2.groups);
      const matsVector = F.createMaterialsVector(builder, result2.materials);
      const matricesVector = F.createMatricesVector(builder, result2.matrices);
      const colorsVector = F.createColorsVector(builder, result2.colors);
      const idsVector2 = F.createIdsVector(builder, result2.ids);
      const itemsSizeVector = F.createItemsSizeVector(builder, itemsSize);
      const idStr = builder.createString(result2.id);
      F.startFragment(builder);
      F.addPosition(builder, posVector);
      F.addNormal(builder, normalVector);
      F.addIndex(builder, indexVector);
      F.addGroups(builder, groupsVector);
      F.addMaterials(builder, matsVector);
      F.addMatrices(builder, matricesVector);
      F.addColors(builder, colorsVector);
      F.addIds(builder, idsVector2);
      F.addItemsSize(builder, itemsSizeVector);
      F.addId(builder, idStr);
      F.addCapacity(builder, fragment.capacity);
      F.addCapacityOffset(builder, fragment.capacityOffset);
      const exported = Fragment$1.endFragment(builder);
      items.push(exported);
    }
    const itemsVector = G.createItemsVector(builder, items);
    const matrixVector = G.createCoordinationMatrixVector(
      builder,
      group.coordinationMatrix.elements
    );
    let fragmentKeys = "";
    for (const fragmentID of group.keyFragments.values()) {
      if (fragmentKeys.length) {
        fragmentKeys += this.fragmentIDSeparator;
      }
      fragmentKeys += fragmentID;
    }
    const fragmentKeysRef = builder.createString(fragmentKeys);
    const keyIndices = [];
    const itemsKeys = [];
    const relsIndices = [];
    const itemsRels = [];
    const ids = [];
    let keysCounter = 0;
    let relsCounter = 0;
    for (const [expressID, [keys, rels]] of group.data) {
      keyIndices.push(keysCounter);
      relsIndices.push(relsCounter);
      ids.push(expressID);
      for (const key of keys) {
        itemsKeys.push(key);
      }
      for (const rel of rels) {
        itemsRels.push(rel);
      }
      keysCounter += keys.length;
      relsCounter += rels.length;
    }
    const opaqueIDs = [];
    const transpIDs = [];
    for (const [geometryID, key] of group.geometryIDs.opaque) {
      opaqueIDs.push(geometryID, key);
    }
    for (const [geometryID, key] of group.geometryIDs.transparent) {
      transpIDs.push(geometryID, key);
    }
    const groupID = builder.createString(group.uuid);
    const groupName = builder.createString(group.name);
    const ifcName = builder.createString(group.ifcMetadata.name);
    const ifcDescription = builder.createString(group.ifcMetadata.description);
    const ifcSchema = builder.createString(group.ifcMetadata.schema);
    const keysIVector = G.createItemsKeysIndicesVector(builder, keyIndices);
    const keysVector = G.createItemsKeysVector(builder, itemsKeys);
    const relsIVector = G.createItemsRelsIndicesVector(builder, relsIndices);
    const relsVector = G.createItemsRelsVector(builder, itemsRels);
    const idsVector = G.createIdsVector(builder, ids);
    const oIdsVector = G.createOpaqueGeometriesIdsVector(builder, opaqueIDs);
    const tIdsVector = G.createTransparentGeometriesIdsVector(
      builder,
      transpIDs
    );
    const { min, max } = group.boundingBox;
    const bbox = [min.x, min.y, min.z, max.x, max.y, max.z];
    const bboxVector = G.createBoundingBoxVector(builder, bbox);
    G.startFragmentsGroup(builder);
    G.addId(builder, groupID);
    G.addName(builder, groupName);
    G.addIfcName(builder, ifcName);
    G.addIfcDescription(builder, ifcDescription);
    G.addIfcSchema(builder, ifcSchema);
    G.addMaxExpressId(builder, group.ifcMetadata.maxExpressID);
    G.addItems(builder, itemsVector);
    G.addFragmentKeys(builder, fragmentKeysRef);
    G.addIds(builder, idsVector);
    G.addItemsKeysIndices(builder, keysIVector);
    G.addItemsKeys(builder, keysVector);
    G.addItemsRelsIndices(builder, relsIVector);
    G.addItemsRels(builder, relsVector);
    G.addCoordinationMatrix(builder, matrixVector);
    G.addBoundingBox(builder, bboxVector);
    G.addOpaqueGeometriesIds(builder, oIdsVector);
    G.addTransparentGeometriesIds(builder, tIdsVector);
    if (civilData !== null) {
      G.addCivil(builder, civilData);
    }
    const result = FragmentsGroup$2.endFragmentsGroup(builder);
    builder.finish(result);
    return builder.asUint8Array();
  }
  setID(fbFragment, fragment) {
    const id = fbFragment.id();
    if (id) {
      fragment.id = id;
      fragment.mesh.uuid = id;
    }
  }
  setInstances(fbFragment, fragment) {
    const matricesData = fbFragment.matricesArray();
    const colorData = fbFragment.colorsArray();
    const ids = fbFragment.idsArray();
    const itemsSize = fbFragment.itemsSizeArray();
    if (!matricesData || !ids || !itemsSize) {
      throw new Error(`Error: Can't load empty fragment!`);
    }
    const items = [];
    let offset = 0;
    for (let i = 0; i < itemsSize.length; i++) {
      const id = ids[i];
      const size = itemsSize[i];
      const transforms = [];
      const colorsArray = [];
      for (let j = 0; j < size; j++) {
        const mStart = offset * 16;
        const matrixArray = matricesData.subarray(mStart, mStart + 17);
        const transform = new THREE.Matrix4().fromArray(matrixArray);
        transforms.push(transform);
        if (colorData) {
          const cStart = offset * 3;
          const [r, g, b] = colorData.subarray(cStart, cStart + 4);
          const color = new THREE.Color(r, g, b);
          colorsArray.push(color);
        }
        offset++;
      }
      const colors = colorsArray.length ? colorsArray : void 0;
      items.push({ id, transforms, colors });
    }
    fragment.add(items);
  }
  constructMaterials(fragment) {
    const materials = fragment.materialsArray();
    const matArray = [];
    if (!materials)
      return matArray;
    for (let i = 0; i < materials.length; i += 5) {
      const opacity = materials[i];
      const transparent = Boolean(materials[i + 1]);
      const red = materials[i + 2];
      const green = materials[i + 3];
      const blue = materials[i + 4];
      const color = new THREE.Color(red, green, blue);
      const material = new THREE.MeshLambertMaterial({
        color,
        opacity,
        transparent
      });
      matArray.push(material);
    }
    return matArray;
  }
  constructFragmentGroup(group) {
    const fragmentsGroup = new FragmentsGroup3();
    const civil = group.civil();
    if (civil) {
      const matArray = civil.coordinationMatrixArray();
      const coordinationMatrix = new THREE.Matrix4();
      if (matArray) {
        coordinationMatrix.fromArray(matArray);
      }
      fragmentsGroup.civilData = { alignments: /* @__PURE__ */ new Map(), coordinationMatrix };
      const aligLength = civil.alignmentsLength();
      for (let i = 0; i < aligLength; i++) {
        const lineMat = new THREE.LineBasicMaterial({ color: 16777215 });
        const alignment = new Alignment3();
        const aligData = civil.alignments(i);
        if (!aligData) {
          throw new Error("Alignment not found!");
        }
        const horLength = aligData.horizontalLength();
        alignment.horizontal = this.constructCivilCurves(
          aligData,
          alignment,
          "horizontal",
          horLength,
          lineMat
        );
        const verLength = aligData.verticalLength();
        alignment.vertical = this.constructCivilCurves(
          aligData,
          alignment,
          "vertical",
          verLength,
          lineMat
        );
        const absLength = aligData.horizontalLength();
        alignment.absolute = this.constructCivilCurves(
          aligData,
          alignment,
          "absolute",
          absLength,
          lineMat
        );
        alignment.initialKP = aligData.initialPk();
        fragmentsGroup.civilData.alignments.set(i, alignment);
      }
    }
    fragmentsGroup.uuid = group.id() || fragmentsGroup.uuid;
    fragmentsGroup.name = group.name() || "";
    fragmentsGroup.ifcMetadata = {
      name: group.ifcName() || "",
      description: group.ifcDescription() || "",
      schema: group.ifcSchema() || "IFC2X3",
      maxExpressID: group.maxExpressId() || 0
    };
    const defaultMatrix = new THREE.Matrix4().elements;
    const matrixArray = group.coordinationMatrixArray() || defaultMatrix;
    const ids = group.idsArray() || new Uint32Array();
    const keysIndices = group.itemsKeysIndicesArray() || new Uint32Array();
    const keysArray = group.itemsKeysArray() || new Uint32Array();
    const relsArray = group.itemsRelsArray() || new Uint32Array();
    const relsIndices = group.itemsRelsIndicesArray() || new Uint32Array();
    const keysIdsString = group.fragmentKeys() || "";
    const keysIdsArray = keysIdsString.split(this.fragmentIDSeparator);
    this.setGroupData(fragmentsGroup, ids, keysIndices, keysArray, 0);
    this.setGroupData(fragmentsGroup, ids, relsIndices, relsArray, 1);
    const opaqueIDs = group.opaqueGeometriesIdsArray() || new Uint32Array();
    const transpIDs = group.transparentGeometriesIdsArray() || new Uint32Array();
    const opaque = /* @__PURE__ */ new Map();
    for (let i = 0; i < opaqueIDs.length - 1; i += 2) {
      const geometryID = opaqueIDs[i];
      const key = opaqueIDs[i + 1];
      opaque.set(geometryID, key);
    }
    const transparent = /* @__PURE__ */ new Map();
    for (let i = 0; i < transpIDs.length - 1; i += 2) {
      const geometryID = transpIDs[i];
      const key = transpIDs[i + 1];
      transparent.set(geometryID, key);
    }
    fragmentsGroup.geometryIDs = { opaque, transparent };
    const bbox = group.boundingBoxArray() || [0, 0, 0, 0, 0, 0];
    const [minX, minY, minZ, maxX, maxY, maxZ] = bbox;
    fragmentsGroup.boundingBox.min.set(minX, minY, minZ);
    fragmentsGroup.boundingBox.max.set(maxX, maxY, maxZ);
    for (let i = 0; i < keysIdsArray.length; i++) {
      fragmentsGroup.keyFragments.set(i, keysIdsArray[i]);
    }
    if (matrixArray.length === 16) {
      fragmentsGroup.coordinationMatrix.fromArray(matrixArray);
    }
    return fragmentsGroup;
  }
  setGroupData(group, ids, indices, array, index) {
    for (let i = 0; i < indices.length; i++) {
      const expressID = ids[i];
      const currentIndex = indices[i];
      const nextIndex = indices[i + 1] || array.length;
      const keys = [];
      for (let j = currentIndex; j < nextIndex; j++) {
        keys.push(array[j]);
      }
      if (!group.data.has(expressID)) {
        group.data.set(expressID, [[], []]);
      }
      const data = group.data.get(expressID);
      if (!data)
        continue;
      data[index] = keys;
    }
  }
  constructGeometry(fragment) {
    const position = fragment.positionArray() || new Float32Array();
    const normal = fragment.normalArray() || new Float32Array();
    const index = fragment.indexArray();
    const groups = fragment.groupsArray();
    if (!index)
      throw new Error("Index not found!");
    const geometry = new THREE.BufferGeometry();
    geometry.setIndex(Array.from(index));
    geometry.setAttribute("position", new THREE.BufferAttribute(position, 3));
    geometry.setAttribute("normal", new THREE.BufferAttribute(normal, 3));
    if (groups) {
      for (let i = 0; i < groups.length; i += 3) {
        const start = groups[i];
        const count = groups[i + 1];
        const materialIndex = groups[i + 2];
        geometry.addGroup(start, count, materialIndex);
      }
    }
    return geometry;
  }
  constructCivilCurves(alignData, alignment, option, length, lineMat) {
    const curves = [];
    for (let i = 0; i < length; i++) {
      const found = alignData[option](i);
      if (!found) {
        throw new Error("Curve not found!");
      }
      const points = found.pointsArray();
      if (points === null) {
        throw new Error("Curve points not found!");
      }
      let data = {};
      const curveData = found.data();
      if (curveData) {
        data = JSON.parse(curveData);
      }
      const geometry = new THREE.EdgesGeometry();
      const posAttr = new THREE.BufferAttribute(points, 3);
      geometry.setAttribute("position", posAttr);
      const index = [];
      for (let i2 = 0; i2 < points.length / 3 - 1; i2++) {
        index.push(i2, i2 + 1);
      }
      geometry.setIndex(index);
      const curveMesh = new CurveMesh(i, data, alignment, geometry, lineMat);
      curves.push(curveMesh.curve);
    }
    return curves;
  }
  saveCivilCurves(curves, builder) {
    const CC = CivilCurve$2;
    const curvesRef = [];
    for (const curve of curves) {
      const attrs = curve.mesh.geometry.attributes;
      const position = attrs.position.array;
      const posVector = CC.createPointsVector(builder, position);
      const dataStr = builder.createString(JSON.stringify(curve.data));
      CC.startCivilCurve(builder);
      CC.addPoints(builder, posVector);
      CC.addData(builder, dataStr);
      const exported = CC.endCivilCurve(builder);
      curvesRef.push(exported);
    }
    return curvesRef;
  }
}
let CivilCurve$1 = class CivilCurve2 {
  constructor() {
    __publicField(this, "bb", null);
    __publicField(this, "bb_pos", 0);
  }
  __init(i, bb) {
    this.bb_pos = i;
    this.bb = bb;
    return this;
  }
  static getRootAsCivilCurve(bb, obj) {
    return (obj || new CivilCurve2()).__init(bb.readInt32(bb.position()) + bb.position(), bb);
  }
  static getSizePrefixedRootAsCivilCurve(bb, obj) {
    bb.setPosition(bb.position() + SIZE_PREFIX_LENGTH);
    return (obj || new CivilCurve2()).__init(bb.readInt32(bb.position()) + bb.position(), bb);
  }
  points(index) {
    const offset = this.bb.__offset(this.bb_pos, 4);
    return offset ? this.bb.readFloat32(this.bb.__vector(this.bb_pos + offset) + index * 4) : 0;
  }
  pointsLength() {
    const offset = this.bb.__offset(this.bb_pos, 4);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  pointsArray() {
    const offset = this.bb.__offset(this.bb_pos, 4);
    return offset ? new Float32Array(this.bb.bytes().buffer, this.bb.bytes().byteOffset + this.bb.__vector(this.bb_pos + offset), this.bb.__vector_len(this.bb_pos + offset)) : null;
  }
  data(optionalEncoding) {
    const offset = this.bb.__offset(this.bb_pos, 6);
    return offset ? this.bb.__string(this.bb_pos + offset, optionalEncoding) : null;
  }
  static startCivilCurve(builder) {
    builder.startObject(2);
  }
  static addPoints(builder, pointsOffset) {
    builder.addFieldOffset(0, pointsOffset, 0);
  }
  static createPointsVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addFloat32(data[i]);
    }
    return builder.endVector();
  }
  static startPointsVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static addData(builder, dataOffset) {
    builder.addFieldOffset(1, dataOffset, 0);
  }
  static endCivilCurve(builder) {
    const offset = builder.endObject();
    return offset;
  }
  static createCivilCurve(builder, pointsOffset, dataOffset) {
    CivilCurve2.startCivilCurve(builder);
    CivilCurve2.addPoints(builder, pointsOffset);
    CivilCurve2.addData(builder, dataOffset);
    return CivilCurve2.endCivilCurve(builder);
  }
};
let Alignment$1 = class Alignment2 {
  constructor() {
    __publicField(this, "bb", null);
    __publicField(this, "bb_pos", 0);
  }
  __init(i, bb) {
    this.bb_pos = i;
    this.bb = bb;
    return this;
  }
  static getRootAsAlignment(bb, obj) {
    return (obj || new Alignment2()).__init(
      bb.readInt32(bb.position()) + bb.position(),
      bb
    );
  }
  static getSizePrefixedRootAsAlignment(bb, obj) {
    bb.setPosition(bb.position() + SIZE_PREFIX_LENGTH);
    return (obj || new Alignment2()).__init(
      bb.readInt32(bb.position()) + bb.position(),
      bb
    );
  }
  vertical(index, obj) {
    const offset = this.bb.__offset(this.bb_pos, 4);
    return offset ? (obj || new CivilCurve$1()).__init(
      this.bb.__indirect(
        this.bb.__vector(this.bb_pos + offset) + index * 4
      ),
      this.bb
    ) : null;
  }
  verticalLength() {
    const offset = this.bb.__offset(this.bb_pos, 4);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  horizontal(index, obj) {
    const offset = this.bb.__offset(this.bb_pos, 6);
    return offset ? (obj || new CivilCurve$1()).__init(
      this.bb.__indirect(
        this.bb.__vector(this.bb_pos + offset) + index * 4
      ),
      this.bb
    ) : null;
  }
  horizontalLength() {
    const offset = this.bb.__offset(this.bb_pos, 6);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  absolute(index, obj) {
    const offset = this.bb.__offset(this.bb_pos, 8);
    return offset ? (obj || new CivilCurve$1()).__init(
      this.bb.__indirect(
        this.bb.__vector(this.bb_pos + offset) + index * 4
      ),
      this.bb
    ) : null;
  }
  absoluteLength() {
    const offset = this.bb.__offset(this.bb_pos, 8);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  initialPk() {
    const offset = this.bb.__offset(this.bb_pos, 10);
    return offset ? this.bb.readFloat32(this.bb_pos + offset) : 0;
  }
  static startAlignment(builder) {
    builder.startObject(4);
  }
  static addVertical(builder, verticalOffset) {
    builder.addFieldOffset(0, verticalOffset, 0);
  }
  static createVerticalVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addOffset(data[i]);
    }
    return builder.endVector();
  }
  static startVerticalVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static addHorizontal(builder, horizontalOffset) {
    builder.addFieldOffset(1, horizontalOffset, 0);
  }
  static createHorizontalVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addOffset(data[i]);
    }
    return builder.endVector();
  }
  static startHorizontalVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static addAbsolute(builder, absoluteOffset) {
    builder.addFieldOffset(2, absoluteOffset, 0);
  }
  static createAbsoluteVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addOffset(data[i]);
    }
    return builder.endVector();
  }
  static startAbsoluteVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static addInitialPk(builder, initialPk) {
    builder.addFieldFloat32(3, initialPk, 0);
  }
  static endAlignment(builder) {
    const offset = builder.endObject();
    return offset;
  }
  static createAlignment(builder, verticalOffset, horizontalOffset, absoluteOffset, initialPk) {
    Alignment2.startAlignment(builder);
    Alignment2.addVertical(builder, verticalOffset);
    Alignment2.addHorizontal(builder, horizontalOffset);
    Alignment2.addAbsolute(builder, absoluteOffset);
    Alignment2.addInitialPk(builder, initialPk);
    return Alignment2.endAlignment(builder);
  }
};
class CivilData2 {
  constructor() {
    __publicField(this, "bb", null);
    __publicField(this, "bb_pos", 0);
  }
  __init(i, bb) {
    this.bb_pos = i;
    this.bb = bb;
    return this;
  }
  static getRootAsCivilData(bb, obj) {
    return (obj || new CivilData2()).__init(
      bb.readInt32(bb.position()) + bb.position(),
      bb
    );
  }
  static getSizePrefixedRootAsCivilData(bb, obj) {
    bb.setPosition(bb.position() + SIZE_PREFIX_LENGTH);
    return (obj || new CivilData2()).__init(
      bb.readInt32(bb.position()) + bb.position(),
      bb
    );
  }
  alignments(index, obj) {
    const offset = this.bb.__offset(this.bb_pos, 4);
    return offset ? (obj || new Alignment$1()).__init(
      this.bb.__indirect(
        this.bb.__vector(this.bb_pos + offset) + index * 4
      ),
      this.bb
    ) : null;
  }
  alignmentsLength() {
    const offset = this.bb.__offset(this.bb_pos, 4);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  coordinationMatrix(index) {
    const offset = this.bb.__offset(this.bb_pos, 6);
    return offset ? this.bb.readFloat32(
      this.bb.__vector(this.bb_pos + offset) + index * 4
    ) : 0;
  }
  coordinationMatrixLength() {
    const offset = this.bb.__offset(this.bb_pos, 6);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  coordinationMatrixArray() {
    const offset = this.bb.__offset(this.bb_pos, 6);
    return offset ? new Float32Array(
      this.bb.bytes().buffer,
      this.bb.bytes().byteOffset + this.bb.__vector(this.bb_pos + offset),
      this.bb.__vector_len(this.bb_pos + offset)
    ) : null;
  }
  static startCivilData(builder) {
    builder.startObject(2);
  }
  static addAlignments(builder, alignmentsOffset) {
    builder.addFieldOffset(0, alignmentsOffset, 0);
  }
  static createAlignmentsVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addOffset(data[i]);
    }
    return builder.endVector();
  }
  static startAlignmentsVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static addCoordinationMatrix(builder, coordinationMatrixOffset) {
    builder.addFieldOffset(1, coordinationMatrixOffset, 0);
  }
  static createCoordinationMatrixVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addFloat32(data[i]);
    }
    return builder.endVector();
  }
  static startCoordinationMatrixVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static endCivilData(builder) {
    const offset = builder.endObject();
    return offset;
  }
  static createCivilData(builder, alignmentsOffset, coordinationMatrixOffset) {
    CivilData2.startCivilData(builder);
    CivilData2.addAlignments(builder, alignmentsOffset);
    CivilData2.addCoordinationMatrix(builder, coordinationMatrixOffset);
    return CivilData2.endCivilData(builder);
  }
}
class Fragment3 {
  constructor() {
    __publicField(this, "bb", null);
    __publicField(this, "bb_pos", 0);
  }
  __init(i, bb) {
    this.bb_pos = i;
    this.bb = bb;
    return this;
  }
  static getRootAsFragment(bb, obj) {
    return (obj || new Fragment3()).__init(bb.readInt32(bb.position()) + bb.position(), bb);
  }
  static getSizePrefixedRootAsFragment(bb, obj) {
    bb.setPosition(bb.position() + SIZE_PREFIX_LENGTH);
    return (obj || new Fragment3()).__init(bb.readInt32(bb.position()) + bb.position(), bb);
  }
  position(index) {
    const offset = this.bb.__offset(this.bb_pos, 4);
    return offset ? this.bb.readFloat32(this.bb.__vector(this.bb_pos + offset) + index * 4) : 0;
  }
  positionLength() {
    const offset = this.bb.__offset(this.bb_pos, 4);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  positionArray() {
    const offset = this.bb.__offset(this.bb_pos, 4);
    return offset ? new Float32Array(this.bb.bytes().buffer, this.bb.bytes().byteOffset + this.bb.__vector(this.bb_pos + offset), this.bb.__vector_len(this.bb_pos + offset)) : null;
  }
  normal(index) {
    const offset = this.bb.__offset(this.bb_pos, 6);
    return offset ? this.bb.readFloat32(this.bb.__vector(this.bb_pos + offset) + index * 4) : 0;
  }
  normalLength() {
    const offset = this.bb.__offset(this.bb_pos, 6);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  normalArray() {
    const offset = this.bb.__offset(this.bb_pos, 6);
    return offset ? new Float32Array(this.bb.bytes().buffer, this.bb.bytes().byteOffset + this.bb.__vector(this.bb_pos + offset), this.bb.__vector_len(this.bb_pos + offset)) : null;
  }
  index(index) {
    const offset = this.bb.__offset(this.bb_pos, 8);
    return offset ? this.bb.readUint32(this.bb.__vector(this.bb_pos + offset) + index * 4) : 0;
  }
  indexLength() {
    const offset = this.bb.__offset(this.bb_pos, 8);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  indexArray() {
    const offset = this.bb.__offset(this.bb_pos, 8);
    return offset ? new Uint32Array(this.bb.bytes().buffer, this.bb.bytes().byteOffset + this.bb.__vector(this.bb_pos + offset), this.bb.__vector_len(this.bb_pos + offset)) : null;
  }
  groups(index) {
    const offset = this.bb.__offset(this.bb_pos, 10);
    return offset ? this.bb.readFloat32(this.bb.__vector(this.bb_pos + offset) + index * 4) : 0;
  }
  groupsLength() {
    const offset = this.bb.__offset(this.bb_pos, 10);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  groupsArray() {
    const offset = this.bb.__offset(this.bb_pos, 10);
    return offset ? new Float32Array(this.bb.bytes().buffer, this.bb.bytes().byteOffset + this.bb.__vector(this.bb_pos + offset), this.bb.__vector_len(this.bb_pos + offset)) : null;
  }
  materials(index) {
    const offset = this.bb.__offset(this.bb_pos, 12);
    return offset ? this.bb.readFloat32(this.bb.__vector(this.bb_pos + offset) + index * 4) : 0;
  }
  materialsLength() {
    const offset = this.bb.__offset(this.bb_pos, 12);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  materialsArray() {
    const offset = this.bb.__offset(this.bb_pos, 12);
    return offset ? new Float32Array(this.bb.bytes().buffer, this.bb.bytes().byteOffset + this.bb.__vector(this.bb_pos + offset), this.bb.__vector_len(this.bb_pos + offset)) : null;
  }
  matrices(index) {
    const offset = this.bb.__offset(this.bb_pos, 14);
    return offset ? this.bb.readFloat32(this.bb.__vector(this.bb_pos + offset) + index * 4) : 0;
  }
  matricesLength() {
    const offset = this.bb.__offset(this.bb_pos, 14);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  matricesArray() {
    const offset = this.bb.__offset(this.bb_pos, 14);
    return offset ? new Float32Array(this.bb.bytes().buffer, this.bb.bytes().byteOffset + this.bb.__vector(this.bb_pos + offset), this.bb.__vector_len(this.bb_pos + offset)) : null;
  }
  colors(index) {
    const offset = this.bb.__offset(this.bb_pos, 16);
    return offset ? this.bb.readFloat32(this.bb.__vector(this.bb_pos + offset) + index * 4) : 0;
  }
  colorsLength() {
    const offset = this.bb.__offset(this.bb_pos, 16);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  colorsArray() {
    const offset = this.bb.__offset(this.bb_pos, 16);
    return offset ? new Float32Array(this.bb.bytes().buffer, this.bb.bytes().byteOffset + this.bb.__vector(this.bb_pos + offset), this.bb.__vector_len(this.bb_pos + offset)) : null;
  }
  itemsSize(index) {
    const offset = this.bb.__offset(this.bb_pos, 18);
    return offset ? this.bb.readUint32(this.bb.__vector(this.bb_pos + offset) + index * 4) : 0;
  }
  itemsSizeLength() {
    const offset = this.bb.__offset(this.bb_pos, 18);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  itemsSizeArray() {
    const offset = this.bb.__offset(this.bb_pos, 18);
    return offset ? new Uint32Array(this.bb.bytes().buffer, this.bb.bytes().byteOffset + this.bb.__vector(this.bb_pos + offset), this.bb.__vector_len(this.bb_pos + offset)) : null;
  }
  ids(index) {
    const offset = this.bb.__offset(this.bb_pos, 20);
    return offset ? this.bb.readUint32(this.bb.__vector(this.bb_pos + offset) + index * 4) : 0;
  }
  idsLength() {
    const offset = this.bb.__offset(this.bb_pos, 20);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  idsArray() {
    const offset = this.bb.__offset(this.bb_pos, 20);
    return offset ? new Uint32Array(this.bb.bytes().buffer, this.bb.bytes().byteOffset + this.bb.__vector(this.bb_pos + offset), this.bb.__vector_len(this.bb_pos + offset)) : null;
  }
  id(optionalEncoding) {
    const offset = this.bb.__offset(this.bb_pos, 22);
    return offset ? this.bb.__string(this.bb_pos + offset, optionalEncoding) : null;
  }
  capacity() {
    const offset = this.bb.__offset(this.bb_pos, 24);
    return offset ? this.bb.readUint32(this.bb_pos + offset) : 0;
  }
  capacityOffset() {
    const offset = this.bb.__offset(this.bb_pos, 26);
    return offset ? this.bb.readUint32(this.bb_pos + offset) : 0;
  }
  static startFragment(builder) {
    builder.startObject(12);
  }
  static addPosition(builder, positionOffset) {
    builder.addFieldOffset(0, positionOffset, 0);
  }
  static createPositionVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addFloat32(data[i]);
    }
    return builder.endVector();
  }
  static startPositionVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static addNormal(builder, normalOffset) {
    builder.addFieldOffset(1, normalOffset, 0);
  }
  static createNormalVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addFloat32(data[i]);
    }
    return builder.endVector();
  }
  static startNormalVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static addIndex(builder, indexOffset) {
    builder.addFieldOffset(2, indexOffset, 0);
  }
  static createIndexVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addInt32(data[i]);
    }
    return builder.endVector();
  }
  static startIndexVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static addGroups(builder, groupsOffset) {
    builder.addFieldOffset(3, groupsOffset, 0);
  }
  static createGroupsVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addFloat32(data[i]);
    }
    return builder.endVector();
  }
  static startGroupsVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static addMaterials(builder, materialsOffset) {
    builder.addFieldOffset(4, materialsOffset, 0);
  }
  static createMaterialsVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addFloat32(data[i]);
    }
    return builder.endVector();
  }
  static startMaterialsVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static addMatrices(builder, matricesOffset) {
    builder.addFieldOffset(5, matricesOffset, 0);
  }
  static createMatricesVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addFloat32(data[i]);
    }
    return builder.endVector();
  }
  static startMatricesVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static addColors(builder, colorsOffset) {
    builder.addFieldOffset(6, colorsOffset, 0);
  }
  static createColorsVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addFloat32(data[i]);
    }
    return builder.endVector();
  }
  static startColorsVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static addItemsSize(builder, itemsSizeOffset) {
    builder.addFieldOffset(7, itemsSizeOffset, 0);
  }
  static createItemsSizeVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addInt32(data[i]);
    }
    return builder.endVector();
  }
  static startItemsSizeVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static addIds(builder, idsOffset) {
    builder.addFieldOffset(8, idsOffset, 0);
  }
  static createIdsVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addInt32(data[i]);
    }
    return builder.endVector();
  }
  static startIdsVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static addId(builder, idOffset) {
    builder.addFieldOffset(9, idOffset, 0);
  }
  static addCapacity(builder, capacity) {
    builder.addFieldInt32(10, capacity, 0);
  }
  static addCapacityOffset(builder, capacityOffset) {
    builder.addFieldInt32(11, capacityOffset, 0);
  }
  static endFragment(builder) {
    const offset = builder.endObject();
    return offset;
  }
  static createFragment(builder, positionOffset, normalOffset, indexOffset, groupsOffset, materialsOffset, matricesOffset, colorsOffset, itemsSizeOffset, idsOffset, idOffset, capacity, capacityOffset) {
    Fragment3.startFragment(builder);
    Fragment3.addPosition(builder, positionOffset);
    Fragment3.addNormal(builder, normalOffset);
    Fragment3.addIndex(builder, indexOffset);
    Fragment3.addGroups(builder, groupsOffset);
    Fragment3.addMaterials(builder, materialsOffset);
    Fragment3.addMatrices(builder, matricesOffset);
    Fragment3.addColors(builder, colorsOffset);
    Fragment3.addItemsSize(builder, itemsSizeOffset);
    Fragment3.addIds(builder, idsOffset);
    Fragment3.addId(builder, idOffset);
    Fragment3.addCapacity(builder, capacity);
    Fragment3.addCapacityOffset(builder, capacityOffset);
    return Fragment3.endFragment(builder);
  }
}
let FragmentsGroup$1 = class FragmentsGroup2 {
  constructor() {
    __publicField(this, "bb", null);
    __publicField(this, "bb_pos", 0);
  }
  __init(i, bb) {
    this.bb_pos = i;
    this.bb = bb;
    return this;
  }
  static getRootAsFragmentsGroup(bb, obj) {
    return (obj || new FragmentsGroup2()).__init(
      bb.readInt32(bb.position()) + bb.position(),
      bb
    );
  }
  static getSizePrefixedRootAsFragmentsGroup(bb, obj) {
    bb.setPosition(bb.position() + SIZE_PREFIX_LENGTH);
    return (obj || new FragmentsGroup2()).__init(
      bb.readInt32(bb.position()) + bb.position(),
      bb
    );
  }
  items(index, obj) {
    const offset = this.bb.__offset(this.bb_pos, 4);
    return offset ? (obj || new Fragment3()).__init(
      this.bb.__indirect(
        this.bb.__vector(this.bb_pos + offset) + index * 4
      ),
      this.bb
    ) : null;
  }
  itemsLength() {
    const offset = this.bb.__offset(this.bb_pos, 4);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  civil(obj) {
    const offset = this.bb.__offset(this.bb_pos, 6);
    return offset ? (obj || new CivilData2()).__init(
      this.bb.__indirect(this.bb_pos + offset),
      this.bb
    ) : null;
  }
  coordinationMatrix(index) {
    const offset = this.bb.__offset(this.bb_pos, 8);
    return offset ? this.bb.readFloat32(
      this.bb.__vector(this.bb_pos + offset) + index * 4
    ) : 0;
  }
  coordinationMatrixLength() {
    const offset = this.bb.__offset(this.bb_pos, 8);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  coordinationMatrixArray() {
    const offset = this.bb.__offset(this.bb_pos, 8);
    return offset ? new Float32Array(
      this.bb.bytes().buffer,
      this.bb.bytes().byteOffset + this.bb.__vector(this.bb_pos + offset),
      this.bb.__vector_len(this.bb_pos + offset)
    ) : null;
  }
  ids(index) {
    const offset = this.bb.__offset(this.bb_pos, 10);
    return offset ? this.bb.readUint32(this.bb.__vector(this.bb_pos + offset) + index * 4) : 0;
  }
  idsLength() {
    const offset = this.bb.__offset(this.bb_pos, 10);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  idsArray() {
    const offset = this.bb.__offset(this.bb_pos, 10);
    return offset ? new Uint32Array(
      this.bb.bytes().buffer,
      this.bb.bytes().byteOffset + this.bb.__vector(this.bb_pos + offset),
      this.bb.__vector_len(this.bb_pos + offset)
    ) : null;
  }
  itemsKeys(index) {
    const offset = this.bb.__offset(this.bb_pos, 12);
    return offset ? this.bb.readUint32(this.bb.__vector(this.bb_pos + offset) + index * 4) : 0;
  }
  itemsKeysLength() {
    const offset = this.bb.__offset(this.bb_pos, 12);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  itemsKeysArray() {
    const offset = this.bb.__offset(this.bb_pos, 12);
    return offset ? new Uint32Array(
      this.bb.bytes().buffer,
      this.bb.bytes().byteOffset + this.bb.__vector(this.bb_pos + offset),
      this.bb.__vector_len(this.bb_pos + offset)
    ) : null;
  }
  itemsKeysIndices(index) {
    const offset = this.bb.__offset(this.bb_pos, 14);
    return offset ? this.bb.readUint32(this.bb.__vector(this.bb_pos + offset) + index * 4) : 0;
  }
  itemsKeysIndicesLength() {
    const offset = this.bb.__offset(this.bb_pos, 14);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  itemsKeysIndicesArray() {
    const offset = this.bb.__offset(this.bb_pos, 14);
    return offset ? new Uint32Array(
      this.bb.bytes().buffer,
      this.bb.bytes().byteOffset + this.bb.__vector(this.bb_pos + offset),
      this.bb.__vector_len(this.bb_pos + offset)
    ) : null;
  }
  itemsRels(index) {
    const offset = this.bb.__offset(this.bb_pos, 16);
    return offset ? this.bb.readUint32(this.bb.__vector(this.bb_pos + offset) + index * 4) : 0;
  }
  itemsRelsLength() {
    const offset = this.bb.__offset(this.bb_pos, 16);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  itemsRelsArray() {
    const offset = this.bb.__offset(this.bb_pos, 16);
    return offset ? new Uint32Array(
      this.bb.bytes().buffer,
      this.bb.bytes().byteOffset + this.bb.__vector(this.bb_pos + offset),
      this.bb.__vector_len(this.bb_pos + offset)
    ) : null;
  }
  itemsRelsIndices(index) {
    const offset = this.bb.__offset(this.bb_pos, 18);
    return offset ? this.bb.readUint32(this.bb.__vector(this.bb_pos + offset) + index * 4) : 0;
  }
  itemsRelsIndicesLength() {
    const offset = this.bb.__offset(this.bb_pos, 18);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  itemsRelsIndicesArray() {
    const offset = this.bb.__offset(this.bb_pos, 18);
    return offset ? new Uint32Array(
      this.bb.bytes().buffer,
      this.bb.bytes().byteOffset + this.bb.__vector(this.bb_pos + offset),
      this.bb.__vector_len(this.bb_pos + offset)
    ) : null;
  }
  fragmentKeys(optionalEncoding) {
    const offset = this.bb.__offset(this.bb_pos, 20);
    return offset ? this.bb.__string(this.bb_pos + offset, optionalEncoding) : null;
  }
  globalIds(optionalEncoding) {
    const offset = this.bb.__offset(this.bb_pos, 22);
    return offset ? this.bb.__string(this.bb_pos + offset, optionalEncoding) : null;
  }
  id(optionalEncoding) {
    const offset = this.bb.__offset(this.bb_pos, 24);
    return offset ? this.bb.__string(this.bb_pos + offset, optionalEncoding) : null;
  }
  name(optionalEncoding) {
    const offset = this.bb.__offset(this.bb_pos, 26);
    return offset ? this.bb.__string(this.bb_pos + offset, optionalEncoding) : null;
  }
  ifcName(optionalEncoding) {
    const offset = this.bb.__offset(this.bb_pos, 28);
    return offset ? this.bb.__string(this.bb_pos + offset, optionalEncoding) : null;
  }
  ifcDescription(optionalEncoding) {
    const offset = this.bb.__offset(this.bb_pos, 30);
    return offset ? this.bb.__string(this.bb_pos + offset, optionalEncoding) : null;
  }
  ifcSchema(optionalEncoding) {
    const offset = this.bb.__offset(this.bb_pos, 32);
    return offset ? this.bb.__string(this.bb_pos + offset, optionalEncoding) : null;
  }
  maxExpressId() {
    const offset = this.bb.__offset(this.bb_pos, 34);
    return offset ? this.bb.readUint32(this.bb_pos + offset) : 0;
  }
  boundingBox(index) {
    const offset = this.bb.__offset(this.bb_pos, 36);
    return offset ? this.bb.readFloat32(
      this.bb.__vector(this.bb_pos + offset) + index * 4
    ) : 0;
  }
  boundingBoxLength() {
    const offset = this.bb.__offset(this.bb_pos, 36);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  boundingBoxArray() {
    const offset = this.bb.__offset(this.bb_pos, 36);
    return offset ? new Float32Array(
      this.bb.bytes().buffer,
      this.bb.bytes().byteOffset + this.bb.__vector(this.bb_pos + offset),
      this.bb.__vector_len(this.bb_pos + offset)
    ) : null;
  }
  opaqueGeometriesIds(index) {
    const offset = this.bb.__offset(this.bb_pos, 38);
    return offset ? this.bb.readInt32(this.bb.__vector(this.bb_pos + offset) + index * 4) : 0;
  }
  opaqueGeometriesIdsLength() {
    const offset = this.bb.__offset(this.bb_pos, 38);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  opaqueGeometriesIdsArray() {
    const offset = this.bb.__offset(this.bb_pos, 38);
    return offset ? new Int32Array(
      this.bb.bytes().buffer,
      this.bb.bytes().byteOffset + this.bb.__vector(this.bb_pos + offset),
      this.bb.__vector_len(this.bb_pos + offset)
    ) : null;
  }
  transparentGeometriesIds(index) {
    const offset = this.bb.__offset(this.bb_pos, 40);
    return offset ? this.bb.readInt32(this.bb.__vector(this.bb_pos + offset) + index * 4) : 0;
  }
  transparentGeometriesIdsLength() {
    const offset = this.bb.__offset(this.bb_pos, 40);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  transparentGeometriesIdsArray() {
    const offset = this.bb.__offset(this.bb_pos, 40);
    return offset ? new Int32Array(
      this.bb.bytes().buffer,
      this.bb.bytes().byteOffset + this.bb.__vector(this.bb_pos + offset),
      this.bb.__vector_len(this.bb_pos + offset)
    ) : null;
  }
  static startFragmentsGroup(builder) {
    builder.startObject(19);
  }
  static addItems(builder, itemsOffset) {
    builder.addFieldOffset(0, itemsOffset, 0);
  }
  static createItemsVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addOffset(data[i]);
    }
    return builder.endVector();
  }
  static startItemsVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static addCivil(builder, civilOffset) {
    builder.addFieldOffset(1, civilOffset, 0);
  }
  static addCoordinationMatrix(builder, coordinationMatrixOffset) {
    builder.addFieldOffset(2, coordinationMatrixOffset, 0);
  }
  static createCoordinationMatrixVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addFloat32(data[i]);
    }
    return builder.endVector();
  }
  static startCoordinationMatrixVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static addIds(builder, idsOffset) {
    builder.addFieldOffset(3, idsOffset, 0);
  }
  static createIdsVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addInt32(data[i]);
    }
    return builder.endVector();
  }
  static startIdsVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static addItemsKeys(builder, itemsKeysOffset) {
    builder.addFieldOffset(4, itemsKeysOffset, 0);
  }
  static createItemsKeysVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addInt32(data[i]);
    }
    return builder.endVector();
  }
  static startItemsKeysVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static addItemsKeysIndices(builder, itemsKeysIndicesOffset) {
    builder.addFieldOffset(5, itemsKeysIndicesOffset, 0);
  }
  static createItemsKeysIndicesVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addInt32(data[i]);
    }
    return builder.endVector();
  }
  static startItemsKeysIndicesVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static addItemsRels(builder, itemsRelsOffset) {
    builder.addFieldOffset(6, itemsRelsOffset, 0);
  }
  static createItemsRelsVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addInt32(data[i]);
    }
    return builder.endVector();
  }
  static startItemsRelsVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static addItemsRelsIndices(builder, itemsRelsIndicesOffset) {
    builder.addFieldOffset(7, itemsRelsIndicesOffset, 0);
  }
  static createItemsRelsIndicesVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addInt32(data[i]);
    }
    return builder.endVector();
  }
  static startItemsRelsIndicesVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static addFragmentKeys(builder, fragmentKeysOffset) {
    builder.addFieldOffset(8, fragmentKeysOffset, 0);
  }
  static addGlobalIds(builder, globalIdsOffset) {
    builder.addFieldOffset(9, globalIdsOffset, 0);
  }
  static addId(builder, idOffset) {
    builder.addFieldOffset(10, idOffset, 0);
  }
  static addName(builder, nameOffset) {
    builder.addFieldOffset(11, nameOffset, 0);
  }
  static addIfcName(builder, ifcNameOffset) {
    builder.addFieldOffset(12, ifcNameOffset, 0);
  }
  static addIfcDescription(builder, ifcDescriptionOffset) {
    builder.addFieldOffset(13, ifcDescriptionOffset, 0);
  }
  static addIfcSchema(builder, ifcSchemaOffset) {
    builder.addFieldOffset(14, ifcSchemaOffset, 0);
  }
  static addMaxExpressId(builder, maxExpressId) {
    builder.addFieldInt32(15, maxExpressId, 0);
  }
  static addBoundingBox(builder, boundingBoxOffset) {
    builder.addFieldOffset(16, boundingBoxOffset, 0);
  }
  static createBoundingBoxVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addFloat32(data[i]);
    }
    return builder.endVector();
  }
  static startBoundingBoxVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static addOpaqueGeometriesIds(builder, opaqueGeometriesIdsOffset) {
    builder.addFieldOffset(17, opaqueGeometriesIdsOffset, 0);
  }
  static createOpaqueGeometriesIdsVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addInt32(data[i]);
    }
    return builder.endVector();
  }
  static startOpaqueGeometriesIdsVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static addTransparentGeometriesIds(builder, transparentGeometriesIdsOffset) {
    builder.addFieldOffset(18, transparentGeometriesIdsOffset, 0);
  }
  static createTransparentGeometriesIdsVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addInt32(data[i]);
    }
    return builder.endVector();
  }
  static startTransparentGeometriesIdsVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static endFragmentsGroup(builder) {
    const offset = builder.endObject();
    return offset;
  }
  static finishFragmentsGroupBuffer(builder, offset) {
    builder.finish(offset);
  }
  static finishSizePrefixedFragmentsGroupBuffer(builder, offset) {
    builder.finish(offset, void 0, true);
  }
};
class ParserV2 {
  constructor() {
    __publicField(this, "version", 2);
    __publicField(this, "separator", "|");
  }
  /** {@link FragmentParser.import} */
  import(bytes) {
    const buffer = new ByteBuffer(bytes);
    const fbFragmentsGroup = FragmentsGroup$1.getRootAsFragmentsGroup(buffer);
    const fragmentsGroup = this.constructFragmentGroup(fbFragmentsGroup);
    const length = fbFragmentsGroup.itemsLength();
    for (let i = 0; i < length; i++) {
      const fbFragment = fbFragmentsGroup.items(i);
      if (!fbFragment)
        continue;
      const geometry = this.constructGeometry(fbFragment);
      const materials = this.constructMaterials(fbFragment);
      const capacity = fbFragment.capacity();
      const fragment = new Fragment$2(geometry, materials, capacity);
      fragment.capacityOffset = fbFragment.capacityOffset();
      this.setInstances(fbFragment, fragment);
      this.setID(fbFragment, fragment);
      fragmentsGroup.items.push(fragment);
      fragmentsGroup.add(fragment.mesh);
    }
    return fragmentsGroup;
  }
  /**
   * Exports the FragmentsGroup to a flatbuffer binary file.
   *
   * @param group - The FragmentsGroup to be exported.
   * @returns The flatbuffer binary file as a Uint8Array.
   */
  export(group) {
    const builder = new Builder(1024);
    const items = [];
    const G = FragmentsGroup$1;
    const F = Fragment3;
    let civilData = null;
    if (group.civilData) {
      const alignments = [];
      const A = Alignment$1;
      const C = CivilData2;
      for (const [_id, alignment] of group.civilData.alignments) {
        const { absolute, horizontal, vertical } = alignment;
        const horCurves = this.saveCivilCurves(horizontal, builder);
        const verCurves = this.saveCivilCurves(vertical, builder);
        const absCurves = this.saveCivilCurves(absolute, builder);
        const horVector = A.createHorizontalVector(builder, horCurves);
        const verVector = A.createVerticalVector(builder, verCurves);
        const absVector = A.createAbsoluteVector(builder, absCurves);
        A.startAlignment(builder);
        A.addHorizontal(builder, horVector);
        A.addVertical(builder, verVector);
        A.addAbsolute(builder, absVector);
        A.addInitialPk(builder, alignment.initialKP);
        const exported = A.endAlignment(builder);
        alignments.push(exported);
      }
      const algVector = C.createAlignmentsVector(builder, alignments);
      const coordVector = C.createCoordinationMatrixVector(
        builder,
        group.coordinationMatrix.elements
      );
      C.startCivilData(builder);
      C.addAlignments(builder, algVector);
      C.addCoordinationMatrix(builder, coordVector);
      civilData = C.endCivilData(builder);
    }
    for (const fragment of group.items) {
      const result2 = fragment.exportData();
      const itemsSize = [];
      for (const itemID of fragment.ids) {
        const instances = fragment.getInstancesIDs(itemID);
        if (!instances) {
          throw new Error("Instances not found!");
        }
        itemsSize.push(instances.size);
      }
      const posVector = F.createPositionVector(builder, result2.position);
      const normalVector = F.createNormalVector(builder, result2.normal);
      const indexVector = F.createIndexVector(builder, result2.index);
      const groupsVector = F.createGroupsVector(builder, result2.groups);
      const matsVector = F.createMaterialsVector(builder, result2.materials);
      const matricesVector = F.createMatricesVector(builder, result2.matrices);
      const colorsVector = F.createColorsVector(builder, result2.colors);
      const idsVector2 = F.createIdsVector(builder, result2.ids);
      const itemsSizeVector = F.createItemsSizeVector(builder, itemsSize);
      const idStr = builder.createString(result2.id);
      F.startFragment(builder);
      F.addPosition(builder, posVector);
      F.addNormal(builder, normalVector);
      F.addIndex(builder, indexVector);
      F.addGroups(builder, groupsVector);
      F.addMaterials(builder, matsVector);
      F.addMatrices(builder, matricesVector);
      F.addColors(builder, colorsVector);
      F.addIds(builder, idsVector2);
      F.addItemsSize(builder, itemsSizeVector);
      F.addId(builder, idStr);
      F.addCapacity(builder, fragment.capacity);
      F.addCapacityOffset(builder, fragment.capacityOffset);
      const exported = Fragment3.endFragment(builder);
      items.push(exported);
    }
    const itemsVector = G.createItemsVector(builder, items);
    const matrixVector = G.createCoordinationMatrixVector(
      builder,
      group.coordinationMatrix.elements
    );
    let fragmentKeys = "";
    for (const fragmentID of group.keyFragments.values()) {
      if (fragmentKeys.length) {
        fragmentKeys += this.separator;
      }
      fragmentKeys += fragmentID;
    }
    let globalIDs = "";
    for (const [globalID] of group.globalToExpressIDs) {
      if (globalIDs.length) {
        globalIDs += this.separator;
      }
      globalIDs += globalID;
    }
    const fragmentKeysRef = builder.createString(fragmentKeys);
    const globalIDsRef = builder.createString(globalIDs);
    const keyIndices = [];
    const itemsKeys = [];
    const relsIndices = [];
    const itemsRels = [];
    const ids = [];
    let keysCounter = 0;
    let relsCounter = 0;
    for (const [expressID, [keys, rels]] of group.data) {
      keyIndices.push(keysCounter);
      relsIndices.push(relsCounter);
      ids.push(expressID);
      for (const key of keys) {
        itemsKeys.push(key);
      }
      for (const rel of rels) {
        itemsRels.push(rel);
      }
      keysCounter += keys.length;
      relsCounter += rels.length;
    }
    const opaqueIDs = [];
    const transpIDs = [];
    for (const [geometryID, key] of group.geometryIDs.opaque) {
      opaqueIDs.push(geometryID, key);
    }
    for (const [geometryID, key] of group.geometryIDs.transparent) {
      transpIDs.push(geometryID, key);
    }
    const groupID = builder.createString(group.uuid);
    const groupName = builder.createString(group.name);
    const ifcName = builder.createString(group.ifcMetadata.name);
    const ifcDescription = builder.createString(group.ifcMetadata.description);
    const ifcSchema = builder.createString(group.ifcMetadata.schema);
    const keysIVector = G.createItemsKeysIndicesVector(builder, keyIndices);
    const keysVector = G.createItemsKeysVector(builder, itemsKeys);
    const relsIVector = G.createItemsRelsIndicesVector(builder, relsIndices);
    const relsVector = G.createItemsRelsVector(builder, itemsRels);
    const idsVector = G.createIdsVector(builder, ids);
    const oIdsVector = G.createOpaqueGeometriesIdsVector(builder, opaqueIDs);
    const tIdsVector = G.createTransparentGeometriesIdsVector(
      builder,
      transpIDs
    );
    const { min, max } = group.boundingBox;
    const bbox = [min.x, min.y, min.z, max.x, max.y, max.z];
    const bboxVector = G.createBoundingBoxVector(builder, bbox);
    G.startFragmentsGroup(builder);
    G.addId(builder, groupID);
    G.addName(builder, groupName);
    G.addIfcName(builder, ifcName);
    G.addIfcDescription(builder, ifcDescription);
    G.addIfcSchema(builder, ifcSchema);
    G.addMaxExpressId(builder, group.ifcMetadata.maxExpressID);
    G.addItems(builder, itemsVector);
    G.addFragmentKeys(builder, fragmentKeysRef);
    G.addGlobalIds(builder, globalIDsRef);
    G.addIds(builder, idsVector);
    G.addItemsKeysIndices(builder, keysIVector);
    G.addItemsKeys(builder, keysVector);
    G.addItemsRelsIndices(builder, relsIVector);
    G.addItemsRels(builder, relsVector);
    G.addCoordinationMatrix(builder, matrixVector);
    G.addBoundingBox(builder, bboxVector);
    G.addOpaqueGeometriesIds(builder, oIdsVector);
    G.addTransparentGeometriesIds(builder, tIdsVector);
    if (civilData !== null) {
      G.addCivil(builder, civilData);
    }
    const result = FragmentsGroup$1.endFragmentsGroup(builder);
    builder.finish(result);
    return builder.asUint8Array();
  }
  setID(fbFragment, fragment) {
    const id = fbFragment.id();
    if (id) {
      fragment.id = id;
      fragment.mesh.uuid = id;
    }
  }
  setInstances(fbFragment, fragment) {
    const matricesData = fbFragment.matricesArray();
    const colorData = fbFragment.colorsArray();
    const ids = fbFragment.idsArray();
    const itemsSize = fbFragment.itemsSizeArray();
    if (!matricesData || !ids || !itemsSize) {
      throw new Error(`Error: Can't load empty fragment!`);
    }
    const items = [];
    let offset = 0;
    for (let i = 0; i < itemsSize.length; i++) {
      const id = ids[i];
      const size = itemsSize[i];
      const transforms = [];
      const colorsArray = [];
      for (let j = 0; j < size; j++) {
        const mStart = offset * 16;
        const matrixArray = matricesData.subarray(mStart, mStart + 17);
        const transform = new THREE.Matrix4().fromArray(matrixArray);
        transforms.push(transform);
        if (colorData) {
          const cStart = offset * 3;
          const [r, g, b] = colorData.subarray(cStart, cStart + 4);
          const color = new THREE.Color(r, g, b);
          colorsArray.push(color);
        }
        offset++;
      }
      const colors = colorsArray.length ? colorsArray : void 0;
      items.push({ id, transforms, colors });
    }
    fragment.add(items);
  }
  constructMaterials(fragment) {
    const materials = fragment.materialsArray();
    const matArray = [];
    if (!materials)
      return matArray;
    for (let i = 0; i < materials.length; i += 5) {
      const opacity = materials[i];
      const transparent = Boolean(materials[i + 1]);
      const red = materials[i + 2];
      const green = materials[i + 3];
      const blue = materials[i + 4];
      const color = new THREE.Color(red, green, blue);
      const material = new THREE.MeshLambertMaterial({
        color,
        opacity,
        transparent
      });
      matArray.push(material);
    }
    return matArray;
  }
  constructFragmentGroup(group) {
    const fragmentsGroup = new FragmentsGroup3();
    const civil = group.civil();
    if (civil) {
      const matArray = civil.coordinationMatrixArray();
      const coordinationMatrix = new THREE.Matrix4();
      if (matArray) {
        coordinationMatrix.fromArray(matArray);
      }
      fragmentsGroup.civilData = { alignments: /* @__PURE__ */ new Map(), coordinationMatrix };
      const aligLength = civil.alignmentsLength();
      for (let i = 0; i < aligLength; i++) {
        const lineMat = new THREE.LineBasicMaterial({ color: 16777215 });
        const alignment = new Alignment3();
        const aligData = civil.alignments(i);
        if (!aligData) {
          throw new Error("Alignment not found!");
        }
        const horLength = aligData.horizontalLength();
        alignment.horizontal = this.constructCivilCurves(
          aligData,
          alignment,
          "horizontal",
          horLength,
          lineMat
        );
        const verLength = aligData.verticalLength();
        alignment.vertical = this.constructCivilCurves(
          aligData,
          alignment,
          "vertical",
          verLength,
          lineMat
        );
        const absLength = aligData.horizontalLength();
        alignment.absolute = this.constructCivilCurves(
          aligData,
          alignment,
          "absolute",
          absLength,
          lineMat
        );
        alignment.initialKP = aligData.initialPk();
        fragmentsGroup.civilData.alignments.set(i, alignment);
      }
    }
    fragmentsGroup.uuid = group.id() || fragmentsGroup.uuid;
    fragmentsGroup.name = group.name() || "";
    fragmentsGroup.ifcMetadata = {
      name: group.ifcName() || "",
      description: group.ifcDescription() || "",
      schema: group.ifcSchema() || "IFC2X3",
      maxExpressID: group.maxExpressId() || 0
    };
    const defaultMatrix = new THREE.Matrix4().elements;
    const matrixArray = group.coordinationMatrixArray() || defaultMatrix;
    const ids = group.idsArray() || new Uint32Array();
    const keysIndices = group.itemsKeysIndicesArray() || new Uint32Array();
    const keysArray = group.itemsKeysArray() || new Uint32Array();
    const relsArray = group.itemsRelsArray() || new Uint32Array();
    const relsIndices = group.itemsRelsIndicesArray() || new Uint32Array();
    const keysIdsString = group.fragmentKeys() || "";
    const keysIdsArray = keysIdsString.split(this.separator);
    const globalIdsString = group.globalIds() || "";
    const globalIdsArray = globalIdsString.split(this.separator);
    this.setGroupData(fragmentsGroup, ids, keysIndices, keysArray, 0);
    this.setGroupData(fragmentsGroup, ids, relsIndices, relsArray, 1);
    const opaqueIDs = group.opaqueGeometriesIdsArray() || new Uint32Array();
    const transpIDs = group.transparentGeometriesIdsArray() || new Uint32Array();
    const opaque = /* @__PURE__ */ new Map();
    for (let i = 0; i < opaqueIDs.length - 1; i += 2) {
      const geometryID = opaqueIDs[i];
      const key = opaqueIDs[i + 1];
      opaque.set(geometryID, key);
    }
    const transparent = /* @__PURE__ */ new Map();
    for (let i = 0; i < transpIDs.length - 1; i += 2) {
      const geometryID = transpIDs[i];
      const key = transpIDs[i + 1];
      transparent.set(geometryID, key);
    }
    fragmentsGroup.geometryIDs = { opaque, transparent };
    const bbox = group.boundingBoxArray() || [0, 0, 0, 0, 0, 0];
    const [minX, minY, minZ, maxX, maxY, maxZ] = bbox;
    fragmentsGroup.boundingBox.min.set(minX, minY, minZ);
    fragmentsGroup.boundingBox.max.set(maxX, maxY, maxZ);
    for (let i = 0; i < keysIdsArray.length; i++) {
      fragmentsGroup.keyFragments.set(i, keysIdsArray[i]);
    }
    if (matrixArray.length === 16) {
      fragmentsGroup.coordinationMatrix.fromArray(matrixArray);
    }
    for (let i = 0; i < ids.length; i++) {
      fragmentsGroup.globalToExpressIDs.set(globalIdsArray[i], ids[i]);
    }
    return fragmentsGroup;
  }
  setGroupData(group, ids, indices, array, index) {
    for (let i = 0; i < indices.length; i++) {
      const expressID = ids[i];
      const currentIndex = indices[i];
      const next = indices[i + 1];
      const nextIndex = next === void 0 ? array.length : next;
      const keys = [];
      for (let j = currentIndex; j < nextIndex; j++) {
        keys.push(array[j]);
      }
      if (!group.data.has(expressID)) {
        group.data.set(expressID, [[], []]);
      }
      const data = group.data.get(expressID);
      if (!data)
        continue;
      data[index] = keys;
    }
  }
  constructGeometry(fragment) {
    const position = fragment.positionArray() || new Float32Array();
    const normal = fragment.normalArray() || new Float32Array();
    const index = fragment.indexArray();
    const groups = fragment.groupsArray();
    if (!index)
      throw new Error("Index not found!");
    const geometry = new THREE.BufferGeometry();
    geometry.setIndex(Array.from(index));
    geometry.setAttribute("position", new THREE.BufferAttribute(position, 3));
    geometry.setAttribute("normal", new THREE.BufferAttribute(normal, 3));
    if (groups) {
      for (let i = 0; i < groups.length; i += 3) {
        const start = groups[i];
        const count = groups[i + 1];
        const materialIndex = groups[i + 2];
        geometry.addGroup(start, count, materialIndex);
      }
    }
    return geometry;
  }
  constructCivilCurves(alignData, alignment, option, length, lineMat) {
    const curves = [];
    for (let i = 0; i < length; i++) {
      const found = alignData[option](i);
      if (!found) {
        throw new Error("Curve not found!");
      }
      const points = found.pointsArray();
      if (points === null) {
        throw new Error("Curve points not found!");
      }
      let data = {};
      const curveData = found.data();
      if (curveData) {
        data = JSON.parse(curveData);
      }
      const geometry = new THREE.EdgesGeometry();
      const posAttr = new THREE.BufferAttribute(points, 3);
      geometry.setAttribute("position", posAttr);
      const index = [];
      for (let i2 = 0; i2 < points.length / 3 - 1; i2++) {
        index.push(i2, i2 + 1);
      }
      geometry.setIndex(index);
      const curveMesh = new CurveMesh(i, data, alignment, geometry, lineMat);
      curves.push(curveMesh.curve);
    }
    return curves;
  }
  saveCivilCurves(curves, builder) {
    const CC = CivilCurve$1;
    const curvesRef = [];
    for (const curve of curves) {
      const attrs = curve.mesh.geometry.attributes;
      const position = attrs.position.array;
      const posVector = CC.createPointsVector(builder, position);
      const dataStr = builder.createString(JSON.stringify(curve.data));
      CC.startCivilCurve(builder);
      CC.addPoints(builder, posVector);
      CC.addData(builder, dataStr);
      const exported = CC.endCivilCurve(builder);
      curvesRef.push(exported);
    }
    return curvesRef;
  }
}
class Serializer {
  constructor() {
    // prettier-ignore
    __publicField(this, "parsers", [
      new ParserV2(),
      new ParserV1()
    ]);
    /** {@link FragmentParser.version} */
    __publicField(this, "version", "auto");
  }
  /** {@link FragmentParser.import} */
  import(bytes) {
    const latestVersion = this.parsers.length;
    if (this.version === "auto") {
      for (let i = 0; i < this.parsers.length; i++) {
        const parser2 = this.parsers[i];
        const result2 = parser2.import(bytes);
        if (Object.keys(result2).length === 0) {
          continue;
        }
        if (i !== 0) {
          const version = this.parsers.length - i;
          this.warnVersion(version, latestVersion);
        }
        return result2;
      }
      throw new Error("No valid parser found for this file");
    }
    this.checkCurrentVersionValid(this.version);
    const index = this.parsers.length - this.version;
    const parser = this.parsers[index];
    const result = parser.import(bytes);
    if (Object.keys(result).length === 0) {
      throw new Error(
        `The given version ${this.version} doesn't match to the given file. Try using "auto" in the version property to handle versions automatically.`
      );
    }
    return result;
  }
  /** {@link FragmentParser.export} */
  export(group) {
    if (this.version === "auto") {
      const latestParser = this.parsers[0];
      return latestParser.export(group);
    }
    this.checkCurrentVersionValid(this.version);
    const index = this.parsers.length - this.version;
    const parser = this.parsers[index];
    return parser.export(group);
  }
  checkCurrentVersionValid(latestVersion) {
    if (this.version === "auto")
      return;
    if (this.version !== latestVersion) {
      this.warnVersion(this.version, latestVersion);
    }
    const isInteger = Number.isInteger(this.version);
    if (!isInteger) {
      throw new Error(
        `Invalid version. Non-automatic versions must an integer.`
      );
    }
    if (this.version < 1 || this.version > latestVersion) {
      throw new Error(
        `Invalid version. Versions range from 1 to ${latestVersion}.`
      );
    }
  }
  warnVersion(version, latestVersion) {
    console.warn(
      `This fragment file version is ${version}. The latest version is ${latestVersion}. To avoid issues, please consider updating your fragments. You can do so by regenerating your fragments from the original IFC file.`
    );
  }
}
class StreamerFileDb {
  constructor(baseDirectory) {
    __publicField(this, "baseDirectory");
    __publicField(this, "maxDeadTime", 6e4);
    __publicField(this, "mode", "buffer");
    __publicField(this, "_memoryCleanTime", 1e4);
    __publicField(this, "_intervalID", null);
    __publicField(this, "_isCleaningMemory", false);
    __publicField(this, "cleanMemory", async () => {
      if (this._isCleaningMemory) {
        return;
      }
      this._isCleaningMemory = true;
      const rootDir = await this.getDir(this.baseDirectory);
      const filesToDelete = /* @__PURE__ */ new Set();
      const now = (/* @__PURE__ */ new Date()).getTime();
      for await (const entry of rootDir.values()) {
        const serializedLastAccessed = localStorage.getItem(entry.name) || "0";
        const lastAccess = parseInt(serializedLastAccessed, 10);
        const deadTime = now - lastAccess;
        if (deadTime > this.maxDeadTime) {
          filesToDelete.add(entry.name);
          localStorage.removeItem(entry.name);
        }
      }
      for (const name of filesToDelete) {
        rootDir.removeEntry(name);
      }
      this._isCleaningMemory = false;
    });
    this.baseDirectory = baseDirectory;
    this.setupMemoryCleanup();
  }
  get memoryCleanTime() {
    return this._memoryCleanTime;
  }
  set memoryCleanTime(value) {
    this._memoryCleanTime = value;
    this.dispose();
    this.setupMemoryCleanup();
  }
  isCached(name) {
    const encodedName = this.encodeName(name);
    return localStorage.getItem(encodedName) !== null;
  }
  async get(name) {
    const encodedName = this.encodeName(name);
    const baseDir = await this.getDir(this.baseDirectory);
    try {
      const fileHandle = await baseDir.getFileHandle(encodedName);
      const file = await fileHandle.getFile();
      this.updateLastAccessTime(encodedName);
      return file;
    } catch (e) {
      return null;
    }
  }
  async add(name, buffer) {
    const encodedName = this.encodeName(name);
    const baseDir = await this.getDir(this.baseDirectory);
    const fileHandle = await baseDir.getFileHandle(encodedName, {
      create: true
    });
    const writable = await fileHandle.createWritable();
    await writable.write(buffer);
    await writable.close();
    this.updateLastAccessTime(encodedName);
  }
  async clear() {
    const baseDir = await this.getDir(this.baseDirectory);
    for await (const [name] of baseDir.entries()) {
      await baseDir.removeEntry(name);
    }
  }
  dispose() {
    if (this._intervalID !== null) {
      window.clearInterval(this._intervalID);
    }
  }
  setupMemoryCleanup() {
    this._intervalID = window.setInterval(
      this.cleanMemory,
      this.memoryCleanTime
    );
  }
  async getDir(path) {
    const root = await navigator.storage.getDirectory();
    return root.getDirectoryHandle(path, {
      create: true
    });
  }
  encodeName(name) {
    const illegalChars = /[\\/:*?"<>|]/g;
    return name.replace(illegalChars, "ñ");
  }
  updateLastAccessTime(encodedName) {
    const now = (/* @__PURE__ */ new Date()).getTime().toString();
    localStorage.setItem(encodedName, now);
  }
}
const _FragmentsGroup = class _FragmentsGroup extends THREE.Group {
  constructor() {
    super(...arguments);
    /**
     * An array of Fragment objects that are part of this group.
     */
    __publicField(this, "items", []);
    /**
     * A THREE.Box3 object representing the bounding box of all fragments in this group.
     */
    __publicField(this, "boundingBox", new THREE.Box3());
    /**
     * A THREE.Matrix4 object representing the coordination matrix of this group.
     */
    __publicField(this, "coordinationMatrix", new THREE.Matrix4());
    /**
     * A Map object where the keys are uints and the values are strings representing fragment IDs.
     * This is used to save memory by mapping keys to fragment IDs.
     */
    __publicField(this, "keyFragments", /* @__PURE__ */ new Map());
    /**
     * A Map object where the keys are global IDs and the values are expressIDs.
     */
    __publicField(this, "globalToExpressIDs", /* @__PURE__ */ new Map());
    /**
     * A Map object where the keys are express IDs and the values are arrays of two arrays.
     * The first array contains fragment keys to which this asset belongs, and the second array contains floor and category IDs.
     */
    __publicField(this, "data", /* @__PURE__ */ new Map());
    /**
     * An object with two Map properties, 'opaque' and 'transparent', representing the geometry IDs and keys of opaque and transparent fragments. They must be distinguished because THREE.js doesn't support transparency per instance in InstancedMesh.
     */
    __publicField(this, "geometryIDs", {
      opaque: /* @__PURE__ */ new Map(),
      transparent: /* @__PURE__ */ new Map()
    });
    /**
     * An object representing metadata about the IFC model defined by the IFC schema.
     */
    __publicField(this, "ifcMetadata", {
      name: "",
      description: "",
      schema: "IFC2X3",
      maxExpressID: 0
    });
    /**
     * An optional object containing civil engineering data.
     */
    __publicField(this, "civilData");
    /**
     * An object containing settings for streaming data, including base URL, base file name, IDs, and types.
     */
    __publicField(this, "streamSettings", {
      baseFileName: "",
      ids: /* @__PURE__ */ new Map(),
      types: /* @__PURE__ */ new Map()
    });
    /**
     * Whether this fragments group is being streamed or not.
     */
    __publicField(this, "isStreamed", false);
    /**
     * A protected property representing local properties of the fragments in this group.
     */
    __publicField(this, "_properties");
  }
  /**
   * A getter that checks if this group has properties, either locally defined or streamed from a data source.
   */
  get hasProperties() {
    const hasLocalProps = this._properties !== void 0;
    const hasStreamProps = this.streamSettings.ids.size !== 0;
    return hasLocalProps || hasStreamProps;
  }
  /**
   * A method to create a map of fragment IDs and express IDs contained within them. This is useful because if you want to get "a chair", it might be made of 4 different geometries, and thus the subsets of 4 different fragments. Using this method, you would get exactly the fragments of where that chair is.
   * @param expressIDs - An iterable of express IDs to create the map for. If not provided, returns the fragment ID map for the whole group.
   * @returns A map where the keys are fragment IDs and the values are sets of express IDs.
   */
  getFragmentMap(expressIDs = this.data.keys()) {
    const fragmentMap = {};
    for (const expressID of expressIDs) {
      const data = this.data.get(expressID);
      if (!data)
        continue;
      for (const key of data[0]) {
        const fragmentID = this.keyFragments.get(key);
        if (fragmentID === void 0)
          continue;
        if (!fragmentMap[fragmentID]) {
          fragmentMap[fragmentID] = /* @__PURE__ */ new Set();
        }
        fragmentMap[fragmentID].add(expressID);
      }
    }
    return fragmentMap;
  }
  /**
   * Method to retrieve the vertices of a specific item within the fragments.
   * This method finds the fragments that contain the specified item,
   * then retrieves the vertices of those fragments.
   *
   * @param itemID - The ID of the item for which to retrieve vertices. Usually, an IFC expressID.
   * @returns An array of THREE.Vector3 objects representing the vertices of the specified item.
   *
   * @example
   * ```typescript
   * const itemVertices = fragmentsGroup.getItemVertices(12345);
   * for (const vertex of itemVertices) {
   *   console.log(`Vertex: ${vertex.x}, ${vertex.y}, ${vertex.z}`);
   * }
   * ```
   */
  getItemVertices(itemID) {
    const vertices = [];
    const fragmentIdMap = this.getFragmentMap([itemID]);
    for (const fragmentID in fragmentIdMap) {
      const fragment = this.items.find(
        (fragment2) => fragment2.id === fragmentID
      );
      if (!fragment)
        continue;
      const itemInstances = fragment.getInstancesIDs(itemID);
      if (!itemInstances)
        continue;
      for (const instance of itemInstances) {
        const matrix = new THREE.Matrix4();
        fragment.mesh.getMatrixAt(instance, matrix);
        for (const vertex of fragment.uniqueVertices) {
          const vector = vertex.clone().applyMatrix4(matrix);
          vertices.push(vector);
        }
      }
    }
    return vertices;
  }
  /**
   * Enables or disables the local property caching system.
   *
   * @param enabled - Whether to enable or disable it.
   */
  static setPropertiesDB(enabled) {
    if (enabled) {
      if (!_FragmentsGroup.propertiesDB) {
        _FragmentsGroup.propertiesDB = new StreamerFileDb(
          "that-open-company-streaming-properties"
        );
      }
    } else if (!enabled) {
      if (_FragmentsGroup.propertiesDB) {
        _FragmentsGroup.propertiesDB.dispose();
      }
    }
  }
  /**
   * Method to dispose of the resources used by the FragmentsGroup.
   *
   * @param disposeResources - If true, also dispose of the resources used by the fragments (geometries and materials). Default is true.
   */
  dispose(disposeResources = true) {
    for (const fragment of this.items) {
      fragment.dispose(disposeResources);
    }
    this.coordinationMatrix = new THREE.Matrix4();
    this.keyFragments.clear();
    this.data.clear();
    this._properties = {};
    this.removeFromParent();
    this.items = [];
    if (this.civilData) {
      const { alignments } = this.civilData;
      for (const [_id, alignment] of alignments) {
        this.disposeAlignment(alignment.vertical);
        this.disposeAlignment(alignment.horizontal);
        this.disposeAlignment(alignment.absolute);
      }
    }
    this.civilData = void 0;
  }
  /**
   * Method to set local properties of the fragments in this group.
   *
   * @param properties - An object containing properties of type IfcProperties.
   * The keys of the object are express IDs as strings, and the values are objects representing the properties of the corresponding express ID.
   *
   * @example
   * ```typescript
   * const properties: IfcProperties = {
   *   "12345": {
   *     name: "Chair",
   *     type: 1001,
   *     color: [0.5, 0.5, 0.5],
   *     //... other properties
   *   },
   *   "67890": {
   *     name: "Table",
   *     type: 1002,
   *     color: [0.8, 0.8, 0.8],
   *     //... other properties
   *   },
   *   //... more properties
   * };
   *
   * fragmentsGroup.setLocalProperties(properties);
   * ```
   */
  setLocalProperties(properties) {
    this._properties = properties;
  }
  /**
   * Method to retrieve the local properties of the fragments in this group.
   *
   * @returns {IfcProperties | undefined} - An object containing properties of type IfcProperties.
   * The keys of the object are express IDs as strings, and the values are objects representing the properties of the corresponding express ID.
   * If no local properties are set, it returns `undefined`.
   *
   * @example
   * ```typescript
   * const properties = fragmentsGroup.getLocalProperties();
   * if (properties) {
   *   for (const id in properties) {
   *     const property = properties[id];
   *     console.log(`ID: ${id}, Name: ${property.name}, Type: ${property.type}`);
   *   }
   * }
   * ```
   */
  getLocalProperties() {
    return this._properties;
  }
  /**
   * Method to retrieve all property IDs from either local properties or streamed properties.
   *
   * @returns {number[]} - An array of property IDs.
   *
   * @example
   * ```typescript
   * const propertyIDs = fragmentsGroup.getAllPropertiesIDs();
   * console.log(propertyIDs); // Output: [12345, 67890,...]
   * ```
   */
  getAllPropertiesIDs() {
    if (this._properties) {
      return Object.keys(this._properties).map((id) => parseInt(id, 10));
    }
    return Array.from(this.streamSettings.ids.keys());
  }
  /**
   * Method to retrieve all property types from either local properties or streamed properties.
   *
   * @returns {number[]} - An array of unique property types.
   *
   * @example
   * ```typescript
   * const propertyTypes = fragmentsGroup.getAllPropertiesTypes();
   * console.log(propertyTypes); // Output: [1001, 1002,...]
   * ```
   */
  getAllPropertiesTypes() {
    if (this._properties) {
      const types = /* @__PURE__ */ new Set();
      for (const id in this._properties) {
        const property = this._properties[id];
        if (property.type !== void 0) {
          types.add(property.type);
        }
      }
      return Array.from(types);
    }
    return Array.from(this.streamSettings.types.keys());
  }
  async getProperties(id) {
    if (this._properties) {
      return this._properties[id] || null;
    }
    const url = this.getPropsURL(id);
    const data = await this.getPropertiesData(url);
    return data ? data[id] : null;
  }
  /**
   * Method to set properties of a specific fragment in this group.
   *
   * @param id - The ID of the fragment for which to set properties.
   * @param value - The new properties to set for the fragment. If null, it deletes the properties for the fragment.
   * @throws Will throw an error if writing streamed properties, as it is not supported yet.
   *
   * @example
   * ```typescript
   * const properties: IfcProperties = {
   *   "12345": {
   *     name: "Chair",
   *     type: 1001,
   *     color: [0.5, 0.5, 0.5],
   *     //... other properties
   *   },
   * };
   *
   * fragmentsGroup.setProperties(12345, properties[12345]);
   * ```
   */
  async setProperties(id, value) {
    if (this._properties) {
      if (value !== null) {
        this._properties[id] = value;
      } else {
        delete this._properties[id];
      }
      return;
    }
    throw new Error("Writing streamed properties not supported yet!");
  }
  /**
   * Method to retrieve all properties of a specific type from either local properties or streamed properties.
   *
   * @param type - The type of properties to retrieve.
   * @returns A Promise that resolves to an object containing properties of type IfcProperties, or null if no properties of the specified type are found.
   *
   * @example
   * ```typescript
   * const type = 1001; // Example type
   * fragmentsGroup.getAllPropertiesOfType(type).then((properties) => {
   *   if (properties) {
   *     for (const id in properties) {
   *       const property = properties[id];
   *       console.log(`ID: ${id}, Name: ${property.name}, Type: ${property.type}`);
   *     }
   *   } else {
   *     console.log(`No properties of type ${type} found.`);
   *   }
   * });
   * ```
   */
  async getAllPropertiesOfType(type) {
    if (this._properties) {
      const result2 = {};
      let found = false;
      for (const id in this._properties) {
        const item = this._properties[id];
        if (item.type === type) {
          result2[item.expressID] = item;
          found = true;
        }
      }
      return found ? result2 : null;
    }
    const { types } = this.streamSettings;
    const fileIDs = types.get(type);
    if (fileIDs === void 0) {
      return null;
    }
    const result = {};
    for (const fileID of fileIDs) {
      const name = this.constructFileName(fileID);
      const data = await this.getPropertiesData(name);
      for (const key in data) {
        result[parseInt(key, 10)] = data[key];
      }
    }
    return result;
  }
  clone(_recursive) {
    throw new Error("Use FragmentsGroup.cloneGroup instead!");
  }
  /**
   * Creates a copy of the whole group or a part of it. Each fragment clone shares the geometry of with its respective original fragment, but has its own InstancedMesh data, so it also needs to be disposed.
   *
   * @param items - Optional - The part of the group to be cloned. If not given, the whole group is cloned.
   *
   */
  cloneGroup(items) {
    const newGroup = new _FragmentsGroup();
    newGroup.coordinationMatrix = this.coordinationMatrix;
    newGroup.position.copy(this.position);
    newGroup.rotation.copy(this.rotation);
    newGroup.scale.copy(this.scale);
    newGroup.updateMatrix();
    newGroup.ifcMetadata = { ...this.ifcMetadata };
    if (!items) {
      items = this.getFragmentMap(this.data.keys());
    }
    const allIDs = /* @__PURE__ */ new Set();
    const fragmentIDConversion = /* @__PURE__ */ new Map();
    for (const fragment of this.items) {
      if (!items[fragment.id]) {
        continue;
      }
      const ids = items[fragment.id];
      const newFragment = fragment.clone(ids);
      fragmentIDConversion.set(fragment.id, newFragment.id);
      newGroup.items.push(newFragment);
      newGroup.add(newFragment.mesh);
      for (const expressID of ids) {
        allIDs.add(expressID);
      }
    }
    for (const id of allIDs) {
      const data = this.data.get(id);
      if (data) {
        newGroup.data.set(id, data);
      }
    }
    for (const [fragKey, fragID] of this.keyFragments) {
      if (fragmentIDConversion.has(fragID)) {
        const newID = fragmentIDConversion.get(fragID);
        if (newID === void 0) {
          throw new Error("Malformed fragment ID map during clone!");
        }
        newGroup.keyFragments.set(fragKey, newID);
      }
    }
    for (const [globalID, expressID] of this.globalToExpressIDs) {
      if (allIDs.has(expressID)) {
        newGroup.globalToExpressIDs.set(globalID, expressID);
      }
    }
    if (this.civilData) {
      newGroup.civilData = {
        coordinationMatrix: this.coordinationMatrix,
        alignments: /* @__PURE__ */ new Map()
      };
    }
    return newGroup;
  }
  getPropsURL(id) {
    const { ids } = this.streamSettings;
    const fileID = ids.get(id);
    if (fileID === void 0) {
      throw new Error("ID not found");
    }
    return this.constructFileName(fileID);
  }
  async getPropertiesData(name) {
    var _a;
    if ((_a = this.streamSettings.baseUrl) == null ? void 0 : _a.length) {
      console.warn(
        "streamSettings.baseUrl is deprecated. Use FragmentsGroup.url instead."
      );
      _FragmentsGroup.url = this.streamSettings.baseUrl;
    }
    let fetched;
    if (_FragmentsGroup.useCache) {
      let found = null;
      if (_FragmentsGroup.propertiesDB) {
        found = await _FragmentsGroup.propertiesDB.get(name);
      }
      if (found) {
        fetched = await found.text();
      } else {
        const dataFromBackend = await _FragmentsGroup.fetch(name);
        fetched = await dataFromBackend.text();
        if (_FragmentsGroup.propertiesDB) {
          const encoder = new TextEncoder();
          const buffer = encoder.encode(fetched);
          await _FragmentsGroup.propertiesDB.add(name, buffer);
        }
      }
    } else {
      const dataFromBackend = await _FragmentsGroup.fetch(name);
      fetched = await dataFromBackend.text();
    }
    return JSON.parse(fetched);
  }
  constructFileName(fileID) {
    if (_FragmentsGroup.constructFileName) {
      return _FragmentsGroup.constructFileName(fileID);
    }
    const { baseFileName } = this.streamSettings;
    return `${baseFileName}-${fileID}`;
  }
  disposeAlignment(alignment) {
    for (const curve of alignment) {
      curve.mesh.geometry.dispose();
      if (Array.isArray(curve.mesh.material)) {
        for (const mat of curve.mesh.material) {
          mat.dispose();
        }
      } else {
        curve.mesh.material.dispose();
      }
    }
    alignment.length = 0;
  }
};
__publicField(_FragmentsGroup, "fetch", async (url) => {
  return fetch(`${_FragmentsGroup.url}${url}`);
});
__publicField(_FragmentsGroup, "constructFileName", null);
/**
 * Default URL for requesting property tiles. Feel free to change this, or override the FragmentsGroup.fetch method for more granular control.
 */
__publicField(_FragmentsGroup, "url", "");
/**
 * Whether to use local cache when streaming properties.
 */
__publicField(_FragmentsGroup, "useCache", true);
/**
 * The object in charge of caching property files locally to save requests over the network.
 */
__publicField(_FragmentsGroup, "propertiesDB", null);
let FragmentsGroup3 = _FragmentsGroup;
class Alignment3 {
  constructor() {
    /**
     * Vertical civil curves in the alignment.
     */
    __publicField(this, "vertical", []);
    /**
     * Horizontal civil curves in the alignment.
     */
    __publicField(this, "horizontal", []);
    /**
     * Absolute civil curves in the alignment.
     */
    __publicField(this, "absolute", []);
    /**
     * Initial KP (Kilometer Point) of the alignment.
     */
    __publicField(this, "initialKP", 0);
  }
  /**
   * Returns the total length of the specified alignment type.
   * @param type - The type of alignment (vertical, horizontal, or absolute).
   * @returns The total length of the specified alignment type.
   */
  getLength(type) {
    let length = 0;
    for (const curve of this[type]) {
      length += curve.getLength();
    }
    return length;
  }
  /**
   * Returns the point at the specified percentage along the specified alignment type.
   * @param percentage - The percentage along the alignment type (between zero and one).
   * @param type - The type of alignment (vertical, horizontal, or absolute).
   * @returns The point at the specified percentage along the specified alignment type.
   * @throws Will throw an error if the percentage is out of range or if the point cannot be computed.
   */
  getPointAt(percentage, type) {
    const found = this.getCurveAt(percentage, type);
    return found.curve.getPointAt(found.percentage);
  }
  // Returns the percentage or null if the point is not contained in this alignment
  getPercentageAt(point, type, tolerance = 0.01) {
    const alignment = this[type];
    let currentLength = 0;
    for (const curve of alignment) {
      const factor = curve.getPercentageAt(point, tolerance);
      const curveLength = curve.getLength();
      if (factor !== null) {
        const foundLength = currentLength + factor * curveLength;
        const totalLength = this.getLength(type);
        return foundLength / totalLength;
      }
      currentLength += curveLength;
    }
    return null;
  }
  /**
   * Returns the curve and the percentage at the specified percentage along the specified alignment type.
   * If the percentage is out of range, it will be clamped to the nearest valid value (0 or 1).
   * If the point cannot be computed, an error will be thrown.
   *
   * @param percentage - The percentage along the alignment type (between zero and one).
   * @param type - The type of alignment (vertical, horizontal, or absolute).
   * @returns An object containing the curve and the percentage along the curve.
   * @throws Will throw an error if the percentage is out of range or if the point cannot be computed.
   */
  getCurveAt(percentage, type) {
    if (percentage < 0) {
      percentage = 0;
    } else if (percentage > 1) {
      percentage = 1;
    }
    const alignment = this[type];
    const alignmentLength = this.getLength(type);
    const targetLength = alignmentLength * percentage;
    let accumulatedLength = 0;
    for (const curve of alignment) {
      const curveLength = curve.getLength();
      if (accumulatedLength + curveLength >= targetLength) {
        const targetCurveLength = targetLength - accumulatedLength;
        const percentage2 = targetCurveLength / curveLength;
        return { curve, percentage: percentage2 };
      }
      accumulatedLength += curveLength;
    }
    throw new Error("Could not compute point!");
  }
}
class CivilCurve3 {
  /**
   * Constructs a new instance of CivilCurve.
   * @param index - The index of the curve.
   * @param mesh - The mesh associated with the curve.
   * @param data - Additional data associated with the curve.
   * @param alignment - The alignment of the curve.
   */
  constructor(index, mesh, data, alignment) {
    /**
     * The index of the curve. An alignment is a sequence of ordered curves, and this is the index of this curve in that sequence.
     */
    __publicField(this, "index");
    /**
     * The THREE.js mesh containing the vertices of the curve.
     */
    __publicField(this, "mesh");
    /**
     * Additional data associated with the curve.
     */
    __publicField(this, "data");
    /**
     * The alignment to which this curve belongs.
     */
    __publicField(this, "alignment");
    this.index = index;
    this.mesh = mesh;
    this.data = data;
    this.alignment = alignment;
  }
  get _index() {
    return this.mesh.geometry.index;
  }
  get _pos() {
    return this.mesh.geometry.attributes.position.array;
  }
  /**
   * Calculates the total length of the curve by summing up the lengths of all segments.
   * @returns The total length of the curve.
   */
  getLength() {
    let length = 0;
    for (let i = 0; i < this._index.array.length - 1; i += 2) {
      const { startPoint, endPoint } = this.getSegment(i);
      length += startPoint.distanceTo(endPoint);
    }
    return length;
  }
  /**
   * Calculates a point on the curve based on the given percentage.
   *
   * @param percentage - The percentage along the curve (between zero and one).
   * @returns A new THREE.Vector3 representing the point on the curve.
   *
   * @remarks
   * The method works by first finding the segment that corresponds to the given percentage.
   * It then normalizes the direction of the segment, multiplies it by the distance to the start of the segment,
   * and adds it to the start point of the segment.
   *
   * @throws Will throw an error if the percentage is outside the range [0, 1].
   */
  getPointAt(percentage) {
    const { startPoint, endPoint, distanceToStart } = this.getSegmentAt(percentage);
    const targetPoint = endPoint.clone();
    targetPoint.sub(startPoint);
    targetPoint.normalize();
    targetPoint.multiplyScalar(distanceToStart);
    targetPoint.add(startPoint);
    return targetPoint;
  }
  /**
   * Calculates a segment of the curve based on the given percentage.
   *
   * @param percentage - The percentage along the curve (between zero and one).
   * @returns An object containing the distance to the start of the segment, the index of the segment, and the start and end points of the segment.
   *
   * @remarks
   * The method works by first finding the segment that corresponds to the given percentage.
   * It then returns an object containing the distance to the start of the segment, the index of the segment, and the start and end points of the segment.
   *
   * @throws Will throw an error if the percentage is outside the range [0, 1].
   */
  getSegmentAt(percentage) {
    if (percentage < 0) {
      percentage = 0;
    } else if (percentage > 1) {
      percentage = 1;
    }
    const totalLength = this.getLength();
    const targetLength = totalLength * percentage;
    let accumulatedLength = 0;
    for (let index = 0; index < this._index.array.length - 1; index += 2) {
      const { startPoint, endPoint } = this.getSegment(index);
      const segmentLength = startPoint.distanceTo(endPoint);
      if (accumulatedLength + segmentLength >= targetLength) {
        const distanceToStart = targetLength - accumulatedLength;
        return { distanceToStart, index, startPoint, endPoint };
      }
      accumulatedLength += segmentLength;
    }
    throw new Error("Could not compute point");
  }
  /**
   * Calculates the percentage of the curve that corresponds to the given point.
   *
   * @param point - The point for which to calculate the percentage.
   * @param tolerance - The tolerance for determining if a point is on the curve. Default is 0.01.
   * @returns The percentage of the curve that corresponds to the given point, or null if the point is not contained in this curve.
   *
   * @remarks
   * The method works by iterating over each segment of the curve and checking if the given point is within the tolerance of the segment.
   * If a point is found, it calculates the percentage of the curve that corresponds to the point.
   * If no point is found, it returns null.
   */
  getPercentageAt(point, tolerance = 0.01) {
    let currentLength = 0;
    for (let i = 0; i < this._index.array.length - 1; i += 2) {
      const { startPoint, endPoint } = this.getSegment(i);
      const segmentLength = startPoint.distanceTo(endPoint);
      const startLength = point.distanceTo(startPoint);
      const endLength = point.distanceTo(endPoint);
      const combinedLength = startLength + endLength;
      const hasPoint = combinedLength - segmentLength <= tolerance;
      if (hasPoint) {
        const foundLength = currentLength + startLength;
        const totalLength = this.getLength();
        return foundLength / totalLength;
      }
      currentLength += segmentLength;
    }
    return null;
  }
  /**
   * Retrieves a segment of the curve based on the given index.
   *
   * @param index - The index of the segment.
   * @returns An object containing the start and end points of the segment.
   *
   * @remarks
   * The method calculates the start and end points of the segment based on the given index.
   * It uses the index array and position attribute of the curve's geometry to determine the start and end points.
   *
   * @throws Will throw an error if the index is out of range.
   */
  getSegment(index) {
    const start = this._index.array[index] * 3;
    const end = this._index.array[index + 1] * 3;
    const startPoint = new THREE.Vector3(
      this._pos[start],
      this._pos[start + 1],
      this._pos[start + 2]
    );
    const endPoint = new THREE.Vector3(
      this._pos[end],
      this._pos[end + 1],
      this._pos[end + 2]
    );
    return { startPoint, endPoint };
  }
}
class CurveMesh extends THREE.LineSegments {
  /**
   * Constructs a new instance of CurveMesh.
   *
   * @param index - The index of the curve mesh.
   * @param data - The data associated with the curve mesh.
   * @param alignment - The alignment of the curve mesh.
   * @param geometry - The geometry for the curve mesh. Optional.
   * @param material - The material(s) for the curve mesh. Optional.
   */
  constructor(index, data, alignment, geometry, material) {
    super(geometry, material);
    /**
     * The civil curve associated with this curve mesh.
     */
    __publicField(this, "curve");
    this.curve = new CivilCurve3(index, this, data, alignment);
  }
}
class StreamedGeometry {
  constructor() {
    __publicField(this, "bb", null);
    __publicField(this, "bb_pos", 0);
  }
  __init(i, bb) {
    this.bb_pos = i;
    this.bb = bb;
    return this;
  }
  static getRootAsStreamedGeometry(bb, obj) {
    return (obj || new StreamedGeometry()).__init(
      bb.readInt32(bb.position()) + bb.position(),
      bb
    );
  }
  static getSizePrefixedRootAsStreamedGeometry(bb, obj) {
    bb.setPosition(bb.position() + SIZE_PREFIX_LENGTH);
    return (obj || new StreamedGeometry()).__init(
      bb.readInt32(bb.position()) + bb.position(),
      bb
    );
  }
  geometryId() {
    const offset = this.bb.__offset(this.bb_pos, 4);
    return offset ? this.bb.readUint32(this.bb_pos + offset) : 0;
  }
  position(index) {
    const offset = this.bb.__offset(this.bb_pos, 6);
    return offset ? this.bb.readFloat32(
      this.bb.__vector(this.bb_pos + offset) + index * 4
    ) : 0;
  }
  positionLength() {
    const offset = this.bb.__offset(this.bb_pos, 6);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  positionArray() {
    const offset = this.bb.__offset(this.bb_pos, 6);
    return offset ? new Float32Array(
      this.bb.bytes().buffer,
      this.bb.bytes().byteOffset + this.bb.__vector(this.bb_pos + offset),
      this.bb.__vector_len(this.bb_pos + offset)
    ) : null;
  }
  normal(index) {
    const offset = this.bb.__offset(this.bb_pos, 8);
    return offset ? this.bb.readFloat32(
      this.bb.__vector(this.bb_pos + offset) + index * 4
    ) : 0;
  }
  normalLength() {
    const offset = this.bb.__offset(this.bb_pos, 8);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  normalArray() {
    const offset = this.bb.__offset(this.bb_pos, 8);
    return offset ? new Float32Array(
      this.bb.bytes().buffer,
      this.bb.bytes().byteOffset + this.bb.__vector(this.bb_pos + offset),
      this.bb.__vector_len(this.bb_pos + offset)
    ) : null;
  }
  index(index) {
    const offset = this.bb.__offset(this.bb_pos, 10);
    return offset ? this.bb.readUint32(this.bb.__vector(this.bb_pos + offset) + index * 4) : 0;
  }
  indexLength() {
    const offset = this.bb.__offset(this.bb_pos, 10);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  indexArray() {
    const offset = this.bb.__offset(this.bb_pos, 10);
    return offset ? new Uint32Array(
      this.bb.bytes().buffer,
      this.bb.bytes().byteOffset + this.bb.__vector(this.bb_pos + offset),
      this.bb.__vector_len(this.bb_pos + offset)
    ) : null;
  }
  static startStreamedGeometry(builder) {
    builder.startObject(4);
  }
  static addGeometryId(builder, geometryId) {
    builder.addFieldInt32(0, geometryId, 0);
  }
  static addPosition(builder, positionOffset) {
    builder.addFieldOffset(1, positionOffset, 0);
  }
  static createPositionVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addFloat32(data[i]);
    }
    return builder.endVector();
  }
  static startPositionVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static addNormal(builder, normalOffset) {
    builder.addFieldOffset(2, normalOffset, 0);
  }
  static createNormalVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addFloat32(data[i]);
    }
    return builder.endVector();
  }
  static startNormalVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static addIndex(builder, indexOffset) {
    builder.addFieldOffset(3, indexOffset, 0);
  }
  static createIndexVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addInt32(data[i]);
    }
    return builder.endVector();
  }
  static startIndexVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static endStreamedGeometry(builder) {
    const offset = builder.endObject();
    return offset;
  }
  static createStreamedGeometry(builder, geometryId, positionOffset, normalOffset, indexOffset) {
    StreamedGeometry.startStreamedGeometry(builder);
    StreamedGeometry.addGeometryId(builder, geometryId);
    StreamedGeometry.addPosition(builder, positionOffset);
    StreamedGeometry.addNormal(builder, normalOffset);
    StreamedGeometry.addIndex(builder, indexOffset);
    return StreamedGeometry.endStreamedGeometry(builder);
  }
}
class StreamedGeometries {
  constructor() {
    __publicField(this, "bb", null);
    __publicField(this, "bb_pos", 0);
  }
  __init(i, bb) {
    this.bb_pos = i;
    this.bb = bb;
    return this;
  }
  static getRootAsStreamedGeometries(bb, obj) {
    return (obj || new StreamedGeometries()).__init(
      bb.readInt32(bb.position()) + bb.position(),
      bb
    );
  }
  static getSizePrefixedRootAsStreamedGeometries(bb, obj) {
    bb.setPosition(bb.position() + SIZE_PREFIX_LENGTH);
    return (obj || new StreamedGeometries()).__init(
      bb.readInt32(bb.position()) + bb.position(),
      bb
    );
  }
  geometries(index, obj) {
    const offset = this.bb.__offset(this.bb_pos, 4);
    return offset ? (obj || new StreamedGeometry()).__init(
      this.bb.__indirect(
        this.bb.__vector(this.bb_pos + offset) + index * 4
      ),
      this.bb
    ) : null;
  }
  geometriesLength() {
    const offset = this.bb.__offset(this.bb_pos, 4);
    return offset ? this.bb.__vector_len(this.bb_pos + offset) : 0;
  }
  static startStreamedGeometries(builder) {
    builder.startObject(1);
  }
  static addGeometries(builder, geometriesOffset) {
    builder.addFieldOffset(0, geometriesOffset, 0);
  }
  static createGeometriesVector(builder, data) {
    builder.startVector(4, data.length, 4);
    for (let i = data.length - 1; i >= 0; i--) {
      builder.addOffset(data[i]);
    }
    return builder.endVector();
  }
  static startGeometriesVector(builder, numElems) {
    builder.startVector(4, numElems, 4);
  }
  static endStreamedGeometries(builder) {
    const offset = builder.endObject();
    return offset;
  }
  static finishStreamedGeometriesBuffer(builder, offset) {
    builder.finish(offset);
  }
  static finishSizePrefixedStreamedGeometriesBuffer(builder, offset) {
    builder.finish(offset, void 0, true);
  }
  static createStreamedGeometries(builder, geometriesOffset) {
    StreamedGeometries.startStreamedGeometries(builder);
    StreamedGeometries.addGeometries(builder, geometriesOffset);
    return StreamedGeometries.endStreamedGeometries(builder);
  }
}
class StreamSerializer {
  /**
   * Imports geometry data from a byte array in a streamed format.
   *
   * @param bytes - The byte array containing the serialized geometry data.
   * @returns A Map of geometry IDs to their respective position, normal, and index arrays.
   * @throws Will throw an error if the geometry ID is not found.
   */
  import(bytes) {
    const buffer = new ByteBuffer(bytes);
    const fbGeoms = StreamedGeometries.getRootAsStreamedGeometries(buffer);
    const geometries = /* @__PURE__ */ new Map();
    const length = fbGeoms.geometriesLength();
    for (let i = 0; i < length; i++) {
      const fbGeom = fbGeoms.geometries(i);
      if (!fbGeom)
        continue;
      const id = fbGeom.geometryId();
      if (id === null) {
        throw new Error("Error finding ID!");
      }
      const position = fbGeom.positionArray();
      const normal = fbGeom.normalArray();
      const index = fbGeom.indexArray();
      if (!position || !normal || !index) {
        continue;
      }
      geometries.set(id, { position, normal, index });
    }
    return geometries;
  }
  /**
   * Exports geometry data to a byte array in a streamed format.
   *
   * @param geometries - A Map of geometry IDs to their respective position, normal, and index arrays.
   * @returns A Uint8Array containing the serialized geometry data.
   */
  export(geometries) {
    const builder = new Builder(1024);
    const createdGeoms = [];
    const Gs = StreamedGeometries;
    const G = StreamedGeometry;
    for (const [id, { index, position, normal }] of geometries) {
      const indexVector = G.createIndexVector(builder, index);
      const posVector = G.createPositionVector(builder, position);
      const norVector = G.createNormalVector(builder, normal);
      G.startStreamedGeometry(builder);
      G.addGeometryId(builder, id);
      G.addIndex(builder, indexVector);
      G.addPosition(builder, posVector);
      G.addNormal(builder, norVector);
      const created = G.endStreamedGeometry(builder);
      createdGeoms.push(created);
    }
    const allGeoms = Gs.createGeometriesVector(builder, createdGeoms);
    Gs.startStreamedGeometries(builder);
    Gs.addGeometries(builder, allGeoms);
    const result = Gs.endStreamedGeometries(builder);
    builder.finish(result);
    return builder.asUint8Array();
  }
}
class FragmentUtils {
  static combine(maps) {
    if (maps.length === 0) {
      return {};
    }
    if (maps.length === 1) {
      return maps[0];
    }
    const result = {};
    for (const map of maps) {
      for (const fragID in map) {
        if (!result[fragID]) {
          result[fragID] = /* @__PURE__ */ new Set();
        }
        for (const expressID of map[fragID]) {
          result[fragID].add(expressID);
        }
      }
    }
    return result;
  }
  static intersect(maps) {
    if (maps.length === 0) {
      return {};
    }
    if (maps.length === 1) {
      return maps[0];
    }
    const visitedIDs = /* @__PURE__ */ new Map();
    let mapsCount = 0;
    for (const map of maps) {
      mapsCount++;
      for (const fragID in map) {
        if (!visitedIDs.has(fragID)) {
          visitedIDs.set(fragID, {
            count: 0,
            ids: /* @__PURE__ */ new Map()
          });
        }
        const current = visitedIDs.get(fragID);
        current.count++;
        for (const id of map[fragID]) {
          const idCount = current.ids.get(id) || 0;
          current.ids.set(id, idCount + 1);
        }
      }
    }
    const result = {};
    for (const [fragID, { count, ids }] of visitedIDs) {
      if (count !== mapsCount) {
        continue;
      }
      for (const [id, idCount] of ids) {
        if (idCount !== mapsCount) {
          continue;
        }
        if (!result[fragID]) {
          result[fragID] = /* @__PURE__ */ new Set();
        }
        result[fragID].add(id);
      }
    }
    return result;
  }
  static copy(map) {
    const copied = {};
    for (const id in map) {
      copied[id] = new Set(map[id]);
    }
    return copied;
  }
  static export(map) {
    const serialized = {};
    for (const fragID in map) {
      serialized[fragID] = Array.from(map[fragID]);
    }
    return serialized;
  }
  static import(serialized) {
    const map = {};
    for (const fragID in serialized) {
      map[fragID] = new Set(serialized[fragID]);
    }
    return map;
  }
}
export {
  Alignment3 as Alignment,
  CivilCurve3 as CivilCurve,
  CurveMesh,
  Fragment$2 as Fragment,
  FragmentMesh,
  FragmentUtils,
  FragmentsGroup3 as FragmentsGroup,
  Serializer,
  StreamSerializer,
  StreamerFileDb
};
