// Proprietary — part of Stokes. Links Fidget (MPL-2.0).

//! Mesh post-processing (ADR-0035 decision 14): exact weld, edge check, fold count, lossless
//! planar simplification and the `STKIMSH1` writer.
//!
//! The mesh is indexed (`v`: f32 positions, `t`: vertex triples, counter-clockwise seen from
//! outside). Everything here is deterministic: the same input gives the same output bytes at any
//! thread count (parallel steps either map element by element or sort by a total order).
use std::collections::HashSet;
use std::io::Write;

use rayon::prelude::*;

#[derive(Clone)]
pub struct TriMesh {
    pub v: Vec<[f32; 3]>,
    pub t: Vec<[u32; 3]>,
}

/// Exact position key; -0.0 counts as +0.0.
pub fn key(p: &[f32; 3]) -> u128 {
    let b = [
        (p[0] + 0.0).to_bits(),
        (p[1] + 0.0).to_bits(),
        (p[2] + 0.0).to_bits(),
    ];
    ((b[0] as u128) << 64) | ((b[1] as u128) << 32) | b[2] as u128
}

pub struct Weld {
    /// vertices merged into another at the same position
    pub merged: usize,
    /// triangles that named one vertex twice after merging (dropped)
    pub collapsed: usize,
    /// coincident opposite triangle pairs dropped
    pub pairs: usize,
    /// vertices pulled apart instead of merged
    pub separated: usize,
    /// per vertex of the result: it was formed by merging
    pub touched: Vec<bool>,
}

impl TriMesh {
    pub fn from_fidget(m: fidget::mesh::Mesh) -> Self {
        let v = m.vertices.par_iter().map(|p| [p.x, p.y, p.z]).collect();
        let t = m
            .triangles
            .par_iter()
            .map(|t| [t.x as u32, t.y as u32, t.z as u32])
            .collect();
        TriMesh { v, t }
    }

    /// Drops triangles that name the same vertex twice; returns how many went.
    ///
    /// Triangles with three distinct vertices but no area stay: they close the surface, and
    /// taking them out leaves open edges.
    pub fn clean(&mut self) -> usize {
        let before = self.t.len();
        self.t
            .retain(|t| !(t[0] == t[1] || t[1] == t[2] || t[2] == t[0]));
        before - self.t.len()
    }

    /// Welds vertices that sit at the same f32 position and drops the triangles that then name
    /// one vertex twice. Returns (vertices merged, triangles dropped).
    ///
    /// Fidget's mesh is closed and manifold by index, but where the model's field jumps across a
    /// surface (for example the seams of a `mod` repetition) it gives two distinct vertices the
    /// same position. An STL keeps positions only, so every reader welds them; welding here
    /// leaves a mesh that is closed and manifold both by index and by position. Never with a
    /// tolerance: distinct sheets can be far less than a cell apart.
    ///
    /// Positions in `separate` are not welded: where welding a group would make an edge used by
    /// four triangles (two sheets of the model touching along a line), its vertices are pulled
    /// apart instead. The member that is an edge-intersection vertex (Fidget writes it last in
    /// every triangle) stays; each other member moves by `step` towards the centroid of its
    /// neighbours, which keeps Fidget's own (manifold) connectivity there and makes every
    /// position distinct. `step` is a few thousandths of a cell.
    pub fn weld(&mut self, separate: &HashSet<u128>, step: f32) -> Weld {
        let n = self.v.len();
        let mut order: Vec<(u128, u32)> = self
            .v
            .par_iter()
            .enumerate()
            .map(|(i, p)| (key(p), i as u32))
            .collect();
        order.par_sort_unstable();
        // representative = lowest index of each run (the tuple sort puts it first)
        let mut rep: Vec<u32> = (0..n as u32).collect();
        let mut moved: Vec<u32> = vec![];
        let mut i = 0;
        let mut is_edge: Option<Vec<bool>> = None;
        while i < order.len() {
            let mut j = i + 1;
            while j < order.len() && order[j].0 == order[i].0 {
                j += 1;
            }
            if j - i > 1 {
                if separate.contains(&order[i].0) {
                    let is_edge = is_edge.get_or_insert_with(|| {
                        let mut e = vec![false; n];
                        for t in &self.t {
                            e[t[2] as usize] = true;
                        }
                        e
                    });
                    let group: Vec<u32> = order[i..j].iter().map(|o| o.1).collect();
                    let keep = group
                        .iter()
                        .copied()
                        .find(|&g| is_edge[g as usize])
                        .unwrap_or(group[0]);
                    moved.extend(group.into_iter().filter(|&g| g != keep));
                } else {
                    for o in &order[i + 1..j] {
                        rep[o.1 as usize] = order[i].1;
                    }
                }
            }
            i = j;
        }
        drop(order);
        let separated = moved.len();
        if !moved.is_empty() {
            self.pull_apart(&moved, step);
        }
        let mut new_index = vec![u32::MAX; n];
        let mut verts = Vec::with_capacity(n);
        for k in 0..n {
            if rep[k] as usize == k {
                new_index[k] = verts.len() as u32;
                verts.push(self.v[k]);
            }
        }
        let merged = n - verts.len();
        let before = self.t.len();
        let mut pairs = 0;
        let mut touched = vec![false; verts.len()];
        if merged > 0 {
            let map: Vec<u32> = rep.iter().map(|&r| new_index[r as usize]).collect();
            // vertices that took part in a merge (new indices)
            for (k, &r) in rep.iter().enumerate() {
                if r as usize != k {
                    touched[map[k] as usize] = true;
                }
            }
            self.t.par_iter_mut().for_each(|t| {
                for c in t.iter_mut() {
                    *c = map[*c as usize];
                }
            });
            self.t
                .retain(|t| !(t[0] == t[1] || t[1] == t[2] || t[2] == t[0]));
            self.v = verts;
            pairs = self.drop_opposite_pairs(&touched);
        }
        Weld {
            merged,
            collapsed: before - self.t.len() - 2 * pairs,
            pairs,
            separated,
            touched,
        }
    }

    /// Moves each listed vertex by `step` towards the centroid of its neighbours, doubling the
    /// step until its position is not taken by any other vertex.
    fn pull_apart(&mut self, moved: &[u32], step: f32) {
        let mut idx = std::collections::HashMap::with_capacity(moved.len());
        for (k, &m) in moved.iter().enumerate() {
            idx.insert(m, k);
        }
        let mut sum = vec![[0f64; 3]; moved.len()];
        let mut cnt = vec![0u32; moved.len()];
        for t in &self.t {
            for k in 0..3 {
                if let Some(&j) = idx.get(&t[k]) {
                    for o in [t[(k + 1) % 3], t[(k + 2) % 3]] {
                        let p = self.v[o as usize];
                        for d in 0..3 {
                            sum[j][d] += f64::from(p[d]);
                        }
                        cnt[j] += 1;
                    }
                }
            }
        }
        let mut taken: HashSet<u128> = self.v.iter().map(key).collect();
        for (j, &m) in moved.iter().enumerate() {
            let p = self.v[m as usize].map(f64::from);
            if cnt[j] == 0 {
                continue;
            }
            let c = sum[j].map(|s| s / f64::from(cnt[j]));
            let d = sub(c, p);
            let l = norm(d);
            if l == 0.0 {
                continue;
            }
            let mut s = f64::from(step).min(0.25 * l);
            for _ in 0..20 {
                let q = [0, 1, 2].map(|k| (p[k] + s * d[k] / l) as f32);
                if !taken.contains(&key(&q)) {
                    taken.insert(key(&q));
                    self.v[m as usize] = q;
                    break;
                }
                s *= 2.0;
            }
        }
    }

    /// After a weld, two triangles can name the same three vertices in opposite order: a
    /// zero-thickness double sheet whose edges are then used four times. Both go (no volume,
    /// no area that bounds anything). Only triangles at welded vertices are examined: Fidget's
    /// own output has no duplicate triangles. Returns the number of pairs dropped.
    fn drop_opposite_pairs(&mut self, touched: &[bool]) -> usize {
        let mut cand: Vec<([u32; 3], bool, u32)> = Vec::new();
        for (i, t) in self.t.iter().enumerate() {
            if t.iter().any(|&c| touched[c as usize]) {
                let mut s = *t;
                s.sort_unstable();
                // even permutation of the sorted triple = same orientation as (s0, s1, s2)
                let even = (t[0] == s[0] && t[1] == s[1])
                    || (t[0] == s[1] && t[1] == s[2])
                    || (t[0] == s[2] && t[1] == s[0]);
                cand.push((s, even, i as u32));
            }
        }
        cand.sort_unstable();
        let mut drop = vec![];
        let mut i = 0;
        while i < cand.len() {
            let mut j = i;
            while j < cand.len() && cand[j].0 == cand[i].0 {
                j += 1;
            }
            let odd: Vec<u32> = cand[i..j].iter().filter(|c| !c.1).map(|c| c.2).collect();
            let even: Vec<u32> = cand[i..j].iter().filter(|c| c.1).map(|c| c.2).collect();
            for k in 0..odd.len().min(even.len()) {
                drop.push(odd[k]);
                drop.push(even[k]);
            }
            i = j;
        }
        if drop.is_empty() {
            return 0;
        }
        let mut keep = vec![true; self.t.len()];
        for &d in &drop {
            keep[d as usize] = false;
        }
        let mut k = 0;
        self.t.retain(|_| {
            k += 1;
            keep[k - 1]
        });
        drop.len() / 2
    }

    /// (volume, area, min, max); f64 sums, the box over the vertices used by triangles.
    pub fn measure(&self) -> (f64, f64, [f32; 3], [f32; 3]) {
        let (vol, area) = self
            .t
            .par_iter()
            .map(|t| {
                let [a, b, c] = t.map(|i| self.v[i as usize].map(|x| x as f64));
                let ab = sub(b, a);
                let ac = sub(c, a);
                (dot(a, cross(b, c)) / 6.0, 0.5 * norm(cross(ab, ac)))
            })
            .reduce(|| (0.0, 0.0), |x, y| (x.0 + y.0, x.1 + y.1));
        let mut used = vec![false; self.v.len()];
        for t in &self.t {
            for &i in t {
                used[i as usize] = true;
            }
        }
        let mut lo = [f32::INFINITY; 3];
        let mut hi = [f32::NEG_INFINITY; 3];
        for (p, &u) in self.v.iter().zip(&used) {
            if u {
                for k in 0..3 {
                    lo[k] = lo[k].min(p[k]);
                    hi[k] = hi[k].max(p[k]);
                }
            }
        }
        (vol, area, lo, hi)
    }

    /// Drops vertices no triangle uses (keeps their order).
    pub fn compact(&mut self) {
        let mut map = vec![u32::MAX; self.v.len()];
        for t in &self.t {
            for &i in t {
                map[i as usize] = 0;
            }
        }
        let mut verts = Vec::with_capacity(self.v.len());
        for (i, m) in map.iter_mut().enumerate() {
            if *m == 0 {
                *m = verts.len() as u32;
                verts.push(self.v[i]);
            }
        }
        if verts.len() == self.v.len() {
            return;
        }
        self.t.par_iter_mut().for_each(|t| {
            for c in t.iter_mut() {
                *c = map[*c as usize];
            }
        });
        self.v = verts;
    }

    /// The `STKIMSH1` file (ADR-0035 decision 14), little-endian: magic, u32 vertex count, u32
    /// triangle count, f32 x,y,z per vertex, u32 a,b,c per triangle, u32 label per triangle.
    ///
    /// The counts must fit in u32; the caller checks that before it builds the mesh.
    pub fn encode(&self, labels: &[u32]) -> Vec<u8> {
        assert_eq!(labels.len(), self.t.len(), "one label per triangle");
        let mut buf = Vec::with_capacity(file_bytes(self) as usize);
        buf.extend_from_slice(MAGIC);
        buf.extend_from_slice(&(self.v.len() as u32).to_le_bytes());
        buf.extend_from_slice(&(self.t.len() as u32).to_le_bytes());
        for p in &self.v {
            for x in p {
                buf.extend_from_slice(&x.to_le_bytes());
            }
        }
        for t in &self.t {
            for x in t {
                buf.extend_from_slice(&x.to_le_bytes());
            }
        }
        for l in labels {
            buf.extend_from_slice(&l.to_le_bytes());
        }
        buf
    }
}

/// First eight bytes of the mesh file.
pub const MAGIC: &[u8; 8] = b"STKIMSH1";

/// Writes `buf` to `path` in 8 MB pieces, adding the bytes to the progress count as they go.
/// The bytes go to `path` + ".part" first and are renamed into place at the end, so a reader
/// never sees a partial file under the final name.
pub fn write_counted(path: &str, buf: &[u8]) -> std::io::Result<()> {
    let part = format!("{path}.part");
    {
        let mut f = std::fs::File::create(&part)?;
        for chunk in buf.chunks(8 << 20) {
            f.write_all(chunk)?;
            fidget::mesh::progress::add(chunk.len() as u64);
        }
        f.sync_all()?;
    }
    // Windows refuses to rename onto an existing file
    if std::path::Path::new(path).exists() {
        std::fs::remove_file(path)?;
    }
    std::fs::rename(&part, path)
}

/// Bytes `TriMesh::encode` gives.
pub fn file_bytes(m: &TriMesh) -> u64 {
    16 + 12 * m.v.len() as u64 + 16 * m.t.len() as u64
}

// ------------------------------------------------------------------------------- small vectors
#[inline]
pub fn sub(a: [f64; 3], b: [f64; 3]) -> [f64; 3] {
    [a[0] - b[0], a[1] - b[1], a[2] - b[2]]
}
#[inline]
pub fn dot(a: [f64; 3], b: [f64; 3]) -> f64 {
    a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
}
#[inline]
pub fn cross(a: [f64; 3], b: [f64; 3]) -> [f64; 3] {
    [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]
}
#[inline]
pub fn norm(a: [f64; 3]) -> f64 {
    dot(a, a).sqrt()
}

// --------------------------------------------------------------------------------- edge check
pub struct Edges {
    /// undirected edges used by exactly one triangle
    pub open: usize,
    /// undirected edges used by three or more triangles
    pub nonmanifold: usize,
    /// edges used by two triangles in the same direction (inconsistent orientation)
    pub misoriented: usize,
    /// opposite half-edge (3 * triangle + corner, from corner k to corner k+1), u32::MAX if
    /// the edge is not a clean pair
    pub opp: Vec<u32>,
    /// vertices on edges of the three kinds above
    pub bad_vertices: Vec<u32>,
}

impl Edges {
    pub fn clean(&self) -> bool {
        self.open == 0 && self.nonmanifold == 0 && self.misoriented == 0
    }
}

#[inline]
pub fn next(h: u32) -> u32 {
    if h % 3 == 2 { h - 2 } else { h + 1 }
}
#[inline]
pub fn prev(h: u32) -> u32 {
    if h.is_multiple_of(3) { h + 2 } else { h - 1 }
}

/// Counts undirected edges by the number of triangles that use them, and pairs half-edges.
///
/// Outgoing half-edges are bucketed per start vertex (CSR); each half-edge a->b looks for a->b
/// and b->a in two short lists. Linear time, parallel over half-edges.
pub fn edges(m: &TriMesh) -> Edges {
    let nv = m.v.len();
    let nh = 3 * m.t.len();
    let corner = |h: usize| m.t[h / 3][h % 3];
    let mut start = vec![0u32; nv + 1];
    for t in &m.t {
        for &a in t {
            start[a as usize + 1] += 1;
        }
    }
    for i in 0..nv {
        start[i + 1] += start[i];
    }
    let mut fill = start.clone();
    let mut out = vec![0u32; nh];
    for h in 0..nh {
        let a = corner(h) as usize;
        out[fill[a] as usize] = h as u32;
        fill[a] += 1;
    }
    drop(fill);
    // per half-edge: (open, nonmanifold representative, misoriented representative, opposite)
    let res: Vec<(u8, u32, bool)> = (0..nh)
        .into_par_iter()
        .map(|h| {
            let a = corner(h);
            let b = corner(next(h as u32) as usize);
            let mut same = 0u32;
            let mut rev = 0u32;
            let mut lowest = h as u32;
            let mut opp = u32::MAX;
            for &g in &out[start[a as usize] as usize..start[a as usize + 1] as usize] {
                if corner(next(g) as usize) == b {
                    same += 1;
                    lowest = lowest.min(g);
                }
            }
            for &g in &out[start[b as usize] as usize..start[b as usize + 1] as usize] {
                if corner(next(g) as usize) == a {
                    rev += 1;
                    lowest = lowest.min(g);
                    opp = g;
                }
            }
            let count = same + rev;
            let rep = lowest == h as u32;
            let flag = if count == 1 {
                1
            } else if count >= 3 && rep {
                2
            } else if count == 2 && same == 2 && rep {
                3
            } else {
                0
            };
            (
                flag,
                if count == 2 && same == 1 {
                    opp
                } else {
                    u32::MAX
                },
                !(count == 2 && same == 1),
            )
        })
        .collect();
    let mut e = Edges {
        open: 0,
        nonmanifold: 0,
        misoriented: 0,
        opp: Vec::with_capacity(nh),
        bad_vertices: vec![],
    };
    for (h, (f, o, bad)) in res.into_iter().enumerate() {
        if bad {
            e.bad_vertices.push(corner(h));
            e.bad_vertices.push(corner(next(h as u32) as usize));
        }
        match f {
            1 => e.open += 1,
            2 => e.nonmanifold += 1,
            3 => e.misoriented += 1,
            _ => {}
        }
        e.opp.push(o);
    }
    e
}

// ------------------------------------------------------------------------------------- folds
/// Two triangles on one edge whose normals are more than this far apart make a fold
/// (ADR-0035 decision 18).
pub const FOLD_ANGLE_DEG: f64 = 170.0;

/// Counts folds: paired edges (`opp` from [`edges`]) whose two triangles have normals more than
/// `angle_deg` apart. A triangle with less than `min_area` has no reliable normal and takes part in no fold. Each
/// undirected edge is counted once. Counting only; removal is slice 4 of ADR-0035.
pub fn folds(m: &TriMesh, opp: &[u32], angle_deg: f64, min_area: f64) -> usize {
    let cos_limit = angle_deg.to_radians().cos();
    // |n| is twice the area: a triangle below `min_area` is degenerate (a needle between two
    // vertices the weld pulled apart) and its normal is rounding noise, so it makes no fold.
    let floor = 2.0 * min_area;
    let normal = |t: usize| {
        let [a, b, c] = m.t[t].map(|i| m.v[i as usize].map(f64::from));
        cross(sub(b, a), sub(c, a))
    };
    opp.par_iter()
        .enumerate()
        .filter(|&(h, &o)| {
            if o == u32::MAX || (o as usize) < h {
                return false;
            }
            let n1 = normal(h / 3);
            let n2 = normal(o as usize / 3);
            let (l1, l2) = (norm(n1), norm(n2));
            let l = l1 * l2;
            // angle > limit  <=>  cos(angle) < cos(limit), with both sides scaled by |n1||n2|
            l1 > floor && l2 > floor && dot(n1, n2) < cos_limit * l
        })
        .count()
}

// --------------------------------------------------------------------- planar simplification
pub struct SimplifyStats {
    pub collapses: usize,
    /// passes over the queue; kept for diagnosis, not reported
    #[allow(dead_code)]
    pub passes: usize,
}

/// Lossless planar simplification by edge collapse onto existing vertices.
///
/// A vertex `v` is collapsed onto a neighbour `u` (no vertex ever moves) only when
/// - the collapse keeps the mesh a closed 2-manifold: link condition (u and v share exactly the
///   two vertices opposite the edge), no tetrahedron collapse;
/// - every triangle of v's fan that survives keeps its orientation and plane: the new normal is
///   within `max_angle` of the old one (so no flip, no fold, nothing leaves its plane) and its
///   area is above a floor (no degenerate triangle);
/// - a per-triangle error bound stays within `tol`: each triangle carries an upper bound of the
///   distance from the original surface it replaces; a collapse adds the distance of v from the
///   new triangles' planes to the largest bound in the fan.
///
/// So a vertex goes only if its whole fan is flat (inside a planar region) or if it lies on a
/// straight feature line between two flat regions and is collapsed along the line. Curved
/// regions, corners and fans with zero-area triangles are left as they are.
///
/// `tol` and `min_area` are in model units (length and length squared); the caller derives them
/// from the cell size, so the rule does not depend on the unit the model is written in.
pub fn simplify(
    m: &mut TriMesh,
    opp: Vec<u32>,
    tol: f64,
    max_angle_deg: f64,
    min_area: f64,
) -> SimplifyStats {
    let nv = m.v.len();
    let nt = m.t.len();
    let pos: Vec<[f64; 3]> = m.v.iter().map(|p| p.map(f64::from)).collect();
    let mut c: Vec<u32> = Vec::with_capacity(3 * nt);
    for t in &m.t {
        c.extend_from_slice(t);
    }
    let mut opp = opp;
    let mut err = vec![0f32; nt];
    let mut vh = vec![u32::MAX; nv];
    // outgoing half-edges per vertex: a fan walk that sees fewer is a vertex where two cones
    // touch (manifold edges, non-manifold vertex); those are left alone
    let mut deg = vec![0u32; nv];
    for &x in &c {
        deg[x as usize] += 1;
    }
    for h in 0..3 * nt {
        vh[c[h] as usize] = h as u32;
    }
    let cos_max = max_angle_deg.to_radians().cos();
    // area floor: a triangle with less area than `min_area` counts as degenerate; compared as
    // |cross|^2 = (2 * area)^2
    let min_cross2 = 4.0 * min_area * min_area;
    let mut stamp = vec![0u32; nv];
    let mut op: u32 = 0;
    let mut queued = vec![true; nv];
    let mut queue: std::collections::VecDeque<u32> = (0..nv as u32).collect();
    let mut stats = SimplifyStats {
        collapses: 0,
        passes: 1,
    };
    const MAX_FAN: usize = 96;
    let mut fan: Vec<u32> = Vec::with_capacity(MAX_FAN);
    let mut cands: Vec<(f64, u32)> = Vec::with_capacity(MAX_FAN);
    let mut ufan: Vec<u32> = Vec::with_capacity(MAX_FAN);
    let mut group: Vec<u8> = Vec::with_capacity(MAX_FAN);
    // creases flatter than 1 degree (and folds) are not treated as feature lines
    let cos_crease = 1f64.to_radians().cos();
    let mut remaining_in_pass = nv;

    // outgoing half-edges of v, rotating; None if the fan is not a single closed cycle
    fn walk(c: &[u32], opp: &[u32], h0: u32, out: &mut Vec<u32>, cap: usize) -> bool {
        out.clear();
        let mut h = h0;
        loop {
            out.push(h);
            if out.len() > cap {
                return false;
            }
            let o = opp[prev(h) as usize];
            if o == u32::MAX {
                return false;
            }
            h = o;
            if h == h0 {
                return true;
            }
            debug_assert_eq!(c[h as usize], c[h0 as usize]);
        }
    }

    let mut popped = 0u64;
    while let Some(v) = queue.pop_front() {
        popped += 1;
        if popped.is_multiple_of(4096) {
            // vertices visited, out of visited + still queued (the queue grows when a collapse
            // re-queues the neighbours, so the total is updated as it goes)
            fidget::mesh::progress::set(popped, popped + queue.len() as u64);
        }
        if remaining_in_pass == 0 {
            stats.passes += 1;
            remaining_in_pass = queue.len() + 1;
        }
        remaining_in_pass -= 1;
        queued[v as usize] = false;
        let h0 = vh[v as usize];
        if h0 == u32::MAX {
            continue;
        }
        if !walk(&c, &opp, h0, &mut fan, MAX_FAN)
            || fan.len() < 3
            || fan.len() != deg[v as usize] as usize
        {
            continue;
        }
        let pv = pos[v as usize];
        // Classify the fan: every triangle must have area and lie in one of at most two planes
        // (unit normals within the angle of the group's first normal). One plane: v is inside
        // a flat region. Two planes meeting along exactly two fan edges (v-p and v-q), a real
        // crease (not a fold: the planes are neither parallel nor antiparallel) and v on the
        // straight segment p-q: v is on a feature line and may only go along it, to p or q.
        let mut ok = true;
        let mut emax = 0f32;
        let mut na = [0f64; 3];
        let mut nb: Option<[f64; 3]> = None;
        group.clear();
        for &h in &fan {
            let a = pos[c[next(h) as usize] as usize];
            let b = pos[c[prev(h) as usize] as usize];
            let n = cross(sub(a, pv), sub(b, pv));
            let l2 = dot(n, n);
            if l2 < min_cross2 {
                ok = false;
                break;
            }
            let n = n.map(|x| x / l2.sqrt());
            emax = emax.max(err[(h / 3) as usize]);
            if group.is_empty() {
                na = n;
                group.push(0u8);
            } else if dot(n, na) >= cos_max {
                group.push(0);
            } else if let Some(m) = nb {
                if dot(n, m) >= cos_max {
                    group.push(1);
                } else {
                    ok = false;
                    break;
                }
            } else {
                nb = Some(n);
                group.push(1);
            }
        }
        if !ok || f64::from(emax) >= tol {
            continue;
        }
        let mut line_d = 0f64;
        cands.clear();
        if let Some(m) = nb {
            if dot(na, m).abs() > cos_crease {
                continue; // a fold or a very shallow crease: leave it
            }
            // the shared edge of fan triangles i and i+1 is v -> end(fan[i+1])
            let k = fan.len();
            let mut feat = [u32::MAX; 2];
            let mut nfeat = 0;
            for i in 0..k {
                if group[i] != group[(i + 1) % k] {
                    if nfeat == 2 {
                        nfeat = 3;
                        break;
                    }
                    feat[nfeat] = fan[(i + 1) % k];
                    nfeat += 1;
                }
            }
            if nfeat != 2 {
                continue;
            }
            let p = pos[c[next(feat[0]) as usize] as usize];
            let q = pos[c[next(feat[1]) as usize] as usize];
            let pq = sub(q, p);
            let l2 = dot(pq, pq);
            let s = dot(sub(pv, p), pq) / l2;
            if !(l2 > 0.0 && s > 0.0 && s < 1.0) {
                continue;
            }
            line_d = norm(cross(sub(pv, p), pq)) / l2.sqrt();
            if f64::from(emax) + line_d > tol {
                continue;
            }
            for h in feat {
                let d = sub(pos[c[next(h) as usize] as usize], pv);
                cands.push((dot(d, d), h));
            }
        } else {
            for &h in &fan {
                let u = c[next(h) as usize];
                let d = sub(pos[u as usize], pv);
                cands.push((dot(d, d), h));
            }
        }
        // nearest first
        cands.sort_by(|x, y| x.0.partial_cmp(&y.0).unwrap());
        op = op.wrapping_add(1);
        if op == 0 {
            stamp.iter_mut().for_each(|s| *s = 0);
            op = 1;
        }
        for &h in &fan {
            stamp[c[next(h) as usize] as usize] = op;
        }
        let valence_v = fan.len();
        for &(_, h) in &cands {
            let u = c[next(h) as usize];
            let pu = pos[u as usize];
            let g = opp[h as usize]; // u -> v
            let w0 = c[prev(h) as usize];
            let w1 = c[prev(g) as usize];
            // link condition (next(h) is u -> w0, an outgoing half-edge of u)
            if !walk(&c, &opp, next(h), &mut ufan, MAX_FAN)
                || ufan.len() != deg[u as usize] as usize
            {
                continue;
            }
            // ufan: outgoing half-edges of u
            let mut common = 0;
            for &k in &ufan {
                let x = c[next(k) as usize];
                if stamp[x as usize] == op {
                    common += 1;
                }
            }
            if common != 2 || (valence_v == 3 && ufan.len() == 3) {
                continue;
            }
            // geometry of the surviving fan triangles
            let t0 = h / 3;
            let t1 = g / 3;
            let mut dmax = 0f64;
            let mut good = true;
            for &k in &fan {
                let t = k / 3;
                if t == t0 || t == t1 {
                    continue;
                }
                let a = pos[c[next(k) as usize] as usize];
                let b = pos[c[prev(k) as usize] as usize];
                let n_old = cross(sub(a, pv), sub(b, pv));
                let n_new = cross(sub(a, pu), sub(b, pu));
                let l2 = dot(n_new, n_new);
                if l2 < min_cross2 {
                    good = false;
                    break;
                }
                let d = dot(n_old, n_new);
                if d <= 0.0 || d * d < cos_max * cos_max * dot(n_old, n_old) * l2 {
                    good = false;
                    break;
                }
                let dist = dot(sub(pv, pu), n_new).abs() / l2.sqrt();
                dmax = dmax.max(dist);
            }
            if !good {
                continue;
            }
            let e_new = f64::from(emax) + dmax.max(line_d);
            if e_new > tol {
                continue;
            }
            // collapse v -> u
            let hn0 = next(h);
            let hp0 = prev(h);
            let on0 = opp[hn0 as usize];
            let op0 = opp[hp0 as usize];
            let gn = next(g);
            let gp = prev(g);
            let ogn = opp[gn as usize];
            let ogp = opp[gp as usize];
            opp[on0 as usize] = op0;
            opp[op0 as usize] = on0;
            opp[ogn as usize] = ogp;
            opp[ogp as usize] = ogn;
            let e32 = (e_new as f32).max(emax);
            for &k in &fan {
                let t = k / 3;
                if t == t0 || t == t1 {
                    continue;
                }
                c[k as usize] = u;
                err[t as usize] = e32;
            }
            for t in [t0, t1] {
                for k in 0..3 {
                    c[(3 * t + k) as usize] = u32::MAX;
                    opp[(3 * t + k) as usize] = u32::MAX;
                }
            }
            vh[u as usize] = op0;
            vh[w0 as usize] = on0;
            vh[w1 as usize] = ogn;
            vh[v as usize] = u32::MAX;
            deg[u as usize] = deg[u as usize] + deg[v as usize] - 4;
            deg[w0 as usize] -= 1;
            deg[w1 as usize] -= 1;
            deg[v as usize] = 0;
            stats.collapses += 1;
            for &k in &fan {
                let x = c[next(k) as usize];
                if x != u32::MAX && !queued[x as usize] {
                    queued[x as usize] = true;
                    queue.push_back(x);
                }
            }
            if !queued[u as usize] {
                queued[u as usize] = true;
                queue.push_back(u);
            }
            break;
        }
    }
    m.t = c
        .as_chunks::<3>()
        .0
        .iter()
        .filter(|t| t[0] != u32::MAX)
        .map(|t| [t[0], t[1], t[2]])
        .collect();
    m.compact();
    stats
}

// ------------------------------------------------------------------------- non-finite vertices
/// Moves every vertex with a non-finite coordinate to the centre of its finite neighbours (over
/// the triangles that use it), projected onto the surface by `project` when it can. Returns how
/// many it moved. Vertices whose neighbours are all non-finite are left (the edge check reports
/// what remains); in practice a NaN vertex is a lone one at a sharp corner.
pub fn repair_nonfinite(
    m: &mut TriMesh,
    project: &mut dyn FnMut([f64; 3]) -> Option<[f64; 3]>,
) -> usize {
    let bad: Vec<bool> =
        m.v.iter()
            .map(|p| p.iter().any(|c| !c.is_finite()))
            .collect();
    if !bad.iter().any(|&b| b) {
        return 0;
    }
    let mut sum = vec![[0.0f64; 3]; m.v.len()];
    let mut count = vec![0u32; m.v.len()];
    for t in &m.t {
        for &i in t {
            if !bad[i as usize] {
                continue;
            }
            for &j in t {
                if !bad[j as usize] {
                    for (total, c) in sum[i as usize].iter_mut().zip(m.v[j as usize]) {
                        *total += f64::from(c);
                    }
                    count[i as usize] += 1;
                }
            }
        }
    }
    let mut moved = 0;
    for i in 0..m.v.len() {
        if bad[i] && count[i] > 0 {
            let n = f64::from(count[i]);
            let centre = sum[i].map(|c| c / n);
            let at = project(centre).unwrap_or(centre);
            m.v[i] = at.map(|c| c as f32);
            moved += 1;
        }
    }
    moved
}

#[cfg(test)]
mod tests {
    use super::*;

    /// One triangle in the z = 0 plane and a second on the shared edge 0-1, oriented
    /// consistently (it uses the edge as 1 -> 0), with its apex at `apex`.
    fn pair(apex: [f32; 3]) -> TriMesh {
        TriMesh {
            v: vec![[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.5, 1.0, 0.0], apex],
            t: vec![[0, 1, 2], [1, 0, 3]],
        }
    }

    #[test]
    fn key_treats_negative_zero_as_zero() {
        assert_eq!(key(&[-0.0, 0.0, -0.0]), key(&[0.0, 0.0, 0.0]));
        assert_ne!(key(&[1.0, 0.0, 0.0]), key(&[0.0, 1.0, 0.0]));
    }

    #[test]
    fn edges_pair_the_shared_edge_and_count_the_open_ones() {
        let m = pair([0.5, -1.0, 0.0]);
        let e = edges(&m);
        assert_eq!(e.open, 4);
        assert_eq!(e.nonmanifold, 0);
        assert_eq!(e.misoriented, 0);
        // half-edge 0 is 0 -> 1 in triangle 0; half-edge 3 is 1 -> 0 in triangle 1
        assert_eq!(e.opp[0], 3);
        assert_eq!(e.opp[3], 0);
        assert!(!e.clean());
    }

    #[test]
    fn a_flat_pair_has_no_fold() {
        let m = pair([0.5, -1.0, 0.0]);
        let e = edges(&m);
        assert_eq!(folds(&m, &e.opp, FOLD_ANGLE_DEG, 0.0), 0);
    }

    #[test]
    fn a_right_angle_is_not_a_fold() {
        let m = pair([0.5, 0.0, 1.0]);
        let e = edges(&m);
        assert_eq!(folds(&m, &e.opp, FOLD_ANGLE_DEG, 0.0), 0);
    }

    #[test]
    fn a_flap_folded_back_onto_its_neighbour_is_one_fold() {
        // the second apex sits on the same side as the first, just above the plane: the
        // normals are about 179 degrees apart
        let m = pair([0.5, 1.0, 0.01]);
        let e = edges(&m);
        assert_eq!(folds(&m, &e.opp, FOLD_ANGLE_DEG, 0.0), 1);
        // and not at a limit beyond the actual angle
        assert_eq!(folds(&m, &e.opp, 179.9, 0.0), 0);
    }

    #[test]
    fn a_degenerate_triangle_takes_part_in_no_fold() {
        let m = pair([0.25, 0.0, 0.0]);
        let e = edges(&m);
        assert_eq!(folds(&m, &e.opp, FOLD_ANGLE_DEG, 0.0), 0);
    }

    #[test]
    fn weld_merges_equal_positions_only() {
        // two triangles that share edge (1,0,0)-(0,1,0) by position but not by index
        let mut m = TriMesh {
            v: vec![
                [0.0, 0.0, 0.0],
                [1.0, 0.0, 0.0],
                [0.0, 1.0, 0.0],
                [1.0, 0.0, 0.0],
                [1.0, 1.0, 0.0],
                [0.0, 1.0, 0.0],
            ],
            t: vec![[0, 1, 2], [3, 4, 5]],
        };
        let w = m.weld(&HashSet::new(), 1e-3);
        assert_eq!(w.merged, 2);
        assert_eq!(w.collapsed, 0);
        assert_eq!(w.pairs, 0);
        assert_eq!(m.v.len(), 4);
        assert_eq!(m.t, vec![[0, 1, 2], [1, 3, 2]]);
        let e = edges(&m);
        assert_eq!(e.open, 4);
        assert_eq!(e.misoriented, 0);
    }

    #[test]
    fn encode_writes_the_documented_layout() {
        let m = pair([0.5, -1.0, 0.0]);
        let labels = vec![0u32; m.t.len()];
        let b = m.encode(&labels);
        assert_eq!(b.len() as u64, file_bytes(&m));
        assert_eq!(&b[..8], MAGIC);
        assert_eq!(u32::from_le_bytes(b[8..12].try_into().unwrap()), 4);
        assert_eq!(u32::from_le_bytes(b[12..16].try_into().unwrap()), 2);
        // second vertex, x
        assert_eq!(f32::from_le_bytes(b[28..32].try_into().unwrap()), 1.0);
        // second triangle, first corner: after 16 header bytes, 48 position bytes, 12 bytes
        assert_eq!(u32::from_le_bytes(b[76..80].try_into().unwrap()), 1);
        // labels at the end
        assert!(b[b.len() - 8..].iter().all(|&x| x == 0));
    }

    /// A unit cube as 12 triangles, outward counter-clockwise, every face split along a diagonal.
    fn cube() -> TriMesh {
        let v: Vec<[f32; 3]> = (0..8)
            .map(|i| [(i & 1) as f32, ((i >> 1) & 1) as f32, ((i >> 2) & 1) as f32])
            .collect();
        let quads = [
            [0, 2, 3, 1], // z = 0, normal -z
            [4, 5, 7, 6], // z = 1, normal +z
            [0, 1, 5, 4], // y = 0, normal -y
            [2, 6, 7, 3], // y = 1, normal +y
            [0, 4, 6, 2], // x = 0, normal -x
            [1, 3, 7, 5], // x = 1, normal +x
        ];
        let mut t = vec![];
        for q in quads {
            t.push([q[0], q[1], q[2]]);
            t.push([q[0], q[2], q[3]]);
        }
        TriMesh { v, t }
    }

    #[test]
    fn a_cube_is_closed_and_has_no_fold() {
        let m = cube();
        let e = edges(&m);
        assert!(e.clean());
        assert_eq!(folds(&m, &e.opp, FOLD_ANGLE_DEG, 0.0), 0);
        let (vol, area, lo, hi) = m.measure();
        assert!((vol - 1.0).abs() < 1e-12);
        assert!((area - 6.0).abs() < 1e-12);
        assert_eq!((lo, hi), ([0.0; 3], [1.0; 3]));
    }

    #[test]
    fn simplify_keeps_a_cube_closed_and_in_place() {
        // the cube has no vertex inside a flat region, so nothing may go, and nothing moves
        let mut m = cube();
        let e = edges(&m);
        let before = m.clone();
        let st = simplify(&mut m, e.opp, 1e-3, 0.1, 1e-12);
        assert_eq!(st.collapses, 0);
        assert_eq!(m.v, before.v);
        assert_eq!(m.t, before.t);
        assert!(edges(&m).clean());
    }
}
