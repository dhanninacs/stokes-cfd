// Proprietary — part of Stokes. Links Fidget (MPL-2.0).

//! Fold removal (ADR-0035 decision 18).
//!
//! Fidget's dual contouring meshes every sign-changing cell edge as a fan of four triangles around
//! the point where the surface crosses that edge, closed by the four cells' vertices. When a cell
//! vertex lands beside or beyond the crossing point, the fan folds: two of its triangles lie on
//! top of each other facing opposite ways. A smooth sphere has them on about a tenth of its
//! triangles. This pass removes them in rounds, each deterministic and in index order:
//!
//! 1. **Vertex removal.** A vertex of valence 3 to 6 (24 in the fallback stage) with a fold among
//!    its triangles (the crossing point of a folded fan, usually) is removed and its ring filled by
//!    the fan from one ring vertex: the fan with the fewest folds, then the one whose normals agree
//!    best.
//! 2. **Edge flips.** A folded edge whose two triangles are still there is flipped.
//! 3. **Edge collapses** (fallback stage only). A short folded edge whose ends share exactly the
//!    two neighbours of its triangles is collapsed to its midpoint projected onto the surface.
//! 4. **Vertex moves** (fallback stage only, with large rings). Where octree cells of different sizes meet, folds sit between vertices
//!    of high valence that neither step can remove. A vertex of a fold still left is moved to the
//!    centre of its ring and projected back onto the surface (`project`, Newton steps on the
//!    field), and kept there if that lowers the folds around it.
//!
//! A change is made only when it strictly lowers the folds among the triangles it touches and
//! their neighbours across its boundary, its new triangles have area, and its new edge is not
//! already an edge of the mesh, so the mesh stays closed and manifold. Every round is checked
//! again by the caller with [`super::meshops::edges`].

use crate::meshops::{TriMesh, cross, dot, next, norm, prev, sub};

/// What the pass did.
#[derive(Debug, Default, Clone, Copy)]
pub struct UnfoldStats {
    pub rounds: usize,
    pub vertices_removed: usize,
    pub flips: usize,
    pub moves: usize,
    pub collapses: usize,
}

/// Projects a point onto the body's surface; None when it cannot (no gradient there).
pub type Project<'p> = dyn FnMut([f64; 3]) -> Option<[f64; 3]> + 'p;

const DEAD: u32 = u32::MAX;
/// The largest ring a removed vertex may have. Fan centres have three or four neighbours; the
/// folds left after the first rounds sit where octree cells of different sizes meet, at vertices
/// of up to about twenty.
const MAX_RING: usize = 24;
/// The largest ring removed before the fallback stage. Larger rings are left to the fallback:
/// filling them gives long thin triangles, so they are only used where nothing else works.
const FIRST_RING: usize = 6;

struct Work<'a> {
    m: &'a TriMesh,
    opp: &'a [u32],
    cos_limit: f64,
    min_area2: f64,
    /// the largest ring [`Work::try_remove`] fills this round
    max_ring: usize,
    /// triangles changed this round (no other change may touch them)
    touched: Vec<bool>,
    /// triangles removed this round
    dead: Vec<bool>,
    new_tris: Vec<[u32; 3]>,
    /// vertices moved this round, with their new positions
    moves: Vec<(u32, [f32; 3])>,
    /// outgoing half-edges per vertex (CSR), for the one-ring and the edge-exists test
    start: Vec<u32>,
    out: Vec<u32>,
}

impl<'a> Work<'a> {
    fn new(m: &'a TriMesh, opp: &'a [u32], angle_deg: f64, min_area: f64) -> Self {
        let nv = m.v.len();
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
        let mut out = vec![0u32; 3 * m.t.len()];
        for (h, slot) in (0..3 * m.t.len()).map(|h| (h, m.t[h / 3][h % 3] as usize)) {
            out[fill[slot] as usize] = h as u32;
            fill[slot] += 1;
        }
        Work {
            m,
            opp,
            cos_limit: angle_deg.to_radians().cos(),
            min_area2: 4.0 * min_area * min_area,
            max_ring: FIRST_RING,
            touched: vec![false; m.t.len()],
            dead: vec![false; m.t.len()],
            new_tris: vec![],
            moves: vec![],
            start,
            out,
        }
    }

    fn corner(&self, h: u32) -> u32 {
        self.m.t[h as usize / 3][h as usize % 3]
    }

    fn pos(&self, v: u32) -> [f64; 3] {
        self.m.v[v as usize].map(f64::from)
    }

    fn normal(&self, t: [u32; 3]) -> [f64; 3] {
        let [a, b, c] = t.map(|i| self.pos(i));
        cross(sub(b, a), sub(c, a))
    }

    fn folded(&self, n1: [f64; 3], n2: [f64; 3]) -> bool {
        // A degenerate triangle has no reliable normal and makes no fold (as `meshops::folds`).
        if dot(n1, n1) <= self.min_area2 || dot(n2, n2) <= self.min_area2 {
            return false;
        }
        dot(n1, n2) < self.cos_limit * norm(n1) * norm(n2)
    }

    /// Whether the undirected edge a-b is an edge of the current mesh.
    fn has_edge(&self, a: u32, b: u32) -> bool {
        let span = |v: u32| {
            &self.out[self.start[v as usize] as usize..self.start[v as usize + 1] as usize]
        };
        span(a).iter().any(|&h| self.corner(next(h)) == b)
            || span(b).iter().any(|&h| self.corner(next(h)) == a)
    }

    /// The one-ring of vertex `v` as (ring vertices in order, the half-edges v -> ring[j]) when
    /// it is a closed fan of paired edges, else None.
    fn ring(&self, v: u32) -> Option<(Vec<u32>, Vec<u32>)> {
        let span = &self.out[self.start[v as usize] as usize..self.start[v as usize + 1] as usize];
        let first = *span.first()?;
        let mut ring = vec![];
        let mut hs = vec![];
        let mut h = first;
        loop {
            ring.push(self.corner(next(h)));
            hs.push(h);
            let back = self.opp[prev(h) as usize];
            if back == DEAD {
                return None;
            }
            h = back;
            if h == first {
                break;
            }
            if ring.len() > span.len() {
                return None;
            }
        }
        (ring.len() == span.len()).then_some((ring, hs))
    }

    /// Folds between `tris` (the replacement) and between them and the triangles across
    /// `boundary` (half-edges of the old configuration on its outline, with their opposite
    /// triangles' normals).
    fn local_folds(&self, tris: &[[u32; 3]], boundary: &[(u32, u32, [f64; 3])]) -> usize {
        let normals: Vec<[f64; 3]> = tris.iter().map(|&t| self.normal(t)).collect();
        let mut count = 0;
        for i in 0..tris.len() {
            for j in i + 1..tris.len() {
                if shares_edge(tris[i], tris[j]) && self.folded(normals[i], normals[j]) {
                    count += 1;
                }
            }
        }
        for &(a, b, outer) in boundary {
            for (t, n) in tris.iter().zip(&normals) {
                if has_directed(*t, a, b) && self.folded(*n, outer) {
                    count += 1;
                }
            }
        }
        count
    }

    fn degenerate(&self, tris: &[[u32; 3]]) -> bool {
        tris.iter().any(|&t| {
            let n = self.normal(t);
            dot(n, n) <= self.min_area2
        })
    }

    /// Replace the fan of `v` (valence 3 or 4) when that lowers the local folds.
    fn try_remove(&mut self, v: u32) -> bool {
        let Some((ring, hs)) = self.ring(v) else {
            return false;
        };
        if !(3..=self.max_ring).contains(&ring.len()) || ring.contains(&v) {
            return false;
        }
        let olds: Vec<usize> = hs.iter().map(|&h| h as usize / 3).collect();
        if olds.iter().any(|&t| self.touched[t]) {
            return false;
        }
        let mut uniq = ring.clone();
        uniq.sort_unstable();
        uniq.dedup();
        if uniq.len() != ring.len() {
            return false;
        }
        // The outline: in triangle (v, r_j, r_j+1) the edge r_j -> r_j+1 is the half-edge after h.
        let mut boundary = vec![];
        for &h in &hs {
            let edge = next(h);
            let other = self.opp[edge as usize];
            if other == DEAD || self.touched[other as usize / 3] {
                return false;
            }
            let outer = self.m.t[other as usize / 3];
            boundary.push((
                self.corner(edge),
                self.corner(next(edge)),
                self.normal(outer),
            ));
        }
        let old: Vec<[u32; 3]> = olds.iter().map(|&t| self.m.t[t]).collect();
        let before = self.local_folds(&old, &boundary);
        if before == 0 {
            return false;
        }
        let k = ring.len();
        // Every fan from one ring vertex: from r_j, the triangles (r_j, r_j+i, r_j+i+1). Its new
        // edges are r_j - r_j+i for i in 2..k-1; none may already be an edge of the mesh.
        let mut best: Option<(usize, f64, Vec<[u32; 3]>)> = None;
        for j in 0..k {
            let r = |i: usize| ring[(j + i) % k];
            let tris: Vec<[u32; 3]> = (1..k - 1).map(|i| [r(0), r(i), r(i + 1)]).collect();
            if self.degenerate(&tris) || (2..k - 1).any(|i| self.has_edge(r(0), r(i))) {
                continue;
            }
            if k == 3 && self.closes_tetrahedron(&ring) {
                continue;
            }
            let after = self.local_folds(&tris, &boundary);
            let agree = self.agreement(&tris);
            // In the fallback stage a removal that leaves as many folds is taken too: it deletes a
            // vertex (so the stage cannot cycle) and opens the way for a flip in the next round.
            let better = after < before || (self.max_ring > FIRST_RING && after == before);
            if better
                && best
                    .as_ref()
                    .is_none_or(|(f, g, _)| after < *f || (after == *f && agree > *g))
            {
                best = Some((after, agree, tris));
            }
        }
        let Some((_, _, tris)) = best else {
            return false;
        };
        for &t in &olds {
            self.touched[t] = true;
            self.dead[t] = true;
        }
        for &h in &hs {
            let other = self.opp[next(h) as usize];
            self.touched[other as usize / 3] = true;
        }
        self.new_tris.extend(tris);
        true
    }

    /// A ring of three whose triangle already exists would close a tetrahedron: removing v would
    /// leave two triangles on the same three vertices.
    fn closes_tetrahedron(&self, ring: &[u32]) -> bool {
        let span = &self.out
            [self.start[ring[0] as usize] as usize..self.start[ring[0] as usize + 1] as usize];
        span.iter().any(|&h| {
            let t = self.m.t[h as usize / 3];
            let mut s = t;
            s.sort_unstable();
            let mut r = [ring[0], ring[1], ring[2]];
            r.sort_unstable();
            s == r
        })
    }

    /// How well the replacement's normals agree with each other: the smallest cosine between two
    /// of its triangles that share an edge, to choose between fans with as few folds.
    fn agreement(&self, tris: &[[u32; 3]]) -> f64 {
        let normals: Vec<[f64; 3]> = tris.iter().map(|&t| self.normal(t)).collect();
        let mut worst: f64 = 1.0;
        for i in 0..tris.len() {
            for j in i + 1..tris.len() {
                if shares_edge(tris[i], tris[j]) {
                    let l = norm(normals[i]) * norm(normals[j]);
                    worst = worst.min(if l > 0.0 {
                        dot(normals[i], normals[j]) / l
                    } else {
                        -1.0
                    });
                }
            }
        }
        worst
    }

    /// Move `v` to the centre of its ring, projected onto the surface, when that lowers the folds
    /// among its triangles and between them and their neighbours.
    fn try_move(&mut self, v: u32, project: &mut Project<'_>) -> bool {
        let Some((ring, hs)) = self.ring(v) else {
            return false;
        };
        let star: Vec<usize> = hs.iter().map(|&h| h as usize / 3).collect();
        let outer: Vec<u32> = hs.iter().map(|&h| self.opp[next(h) as usize]).collect();
        if star.iter().any(|&t| self.touched[t])
            || outer
                .iter()
                .any(|&o| o == DEAD || self.touched[o as usize / 3])
        {
            return false;
        }
        let mut centre = [0.0f64; 3];
        for &r in &ring {
            let q = self.pos(r);
            for k in 0..3 {
                centre[k] += q[k] / ring.len() as f64;
            }
        }
        let Some(target) = project(centre) else {
            return false;
        };
        let target32 = target.map(|c| c as f32);
        if target32.iter().any(|c| !c.is_finite()) {
            return false;
        }
        let tris: Vec<[u32; 3]> = star.iter().map(|&t| self.m.t[t]).collect();
        let outer_normals: Vec<[f64; 3]> = outer
            .iter()
            .map(|&o| self.normal(self.m.t[o as usize / 3]))
            .collect();
        let count = |at: [f64; 3]| -> (usize, bool) {
            let normal = |t: [u32; 3]| {
                let [a, b, c] = t.map(|i| if i == v { at } else { self.pos(i) });
                cross(sub(b, a), sub(c, a))
            };
            let ns: Vec<[f64; 3]> = tris.iter().map(|&t| normal(t)).collect();
            let degenerate = ns.iter().any(|n| dot(*n, *n) <= self.min_area2);
            let mut f = 0;
            for i in 0..tris.len() {
                for j in i + 1..tris.len() {
                    if shares_edge(tris[i], tris[j]) && self.folded(ns[i], ns[j]) {
                        f += 1;
                    }
                }
            }
            for (i, n) in outer_normals.iter().enumerate() {
                if self.folded(ns[i], *n) {
                    f += 1;
                }
            }
            (f, degenerate)
        };
        let (before, _) = count(self.pos(v));
        let (after, degenerate) = count(target32.map(f64::from));
        if degenerate || after >= before {
            return false;
        }
        for &t in &star {
            self.touched[t] = true;
        }
        for &o in &outer {
            self.touched[o as usize / 3] = true;
        }
        self.moves.push((v, target32));
        true
    }

    /// The triangles around `v`, or None when its fan is not closed.
    fn star(&self, v: u32) -> Option<Vec<usize>> {
        self.ring(v)
            .map(|(_, hs)| hs.iter().map(|&h| h as usize / 3).collect())
    }

    /// Collapse the folded edge `h` (a -> b) into one vertex at its projected midpoint when that
    /// does not add folds. The link condition (the ends share exactly the two opposite vertices)
    /// keeps the mesh manifold; the collapse deletes a vertex, so the fallback stage cannot cycle.
    fn try_collapse(&mut self, h: u32, project: &mut Project<'_>) -> bool {
        let o = self.opp[h as usize];
        if o == DEAD {
            return false;
        }
        let (a, b) = (self.corner(h), self.corner(next(h)));
        let (c, d) = (self.corner(prev(h)), self.corner(prev(o)));
        let (Some((ring_a, _)), Some((ring_b, _))) = (self.ring(a), self.ring(b)) else {
            return false;
        };
        let mut common: Vec<u32> = ring_a
            .iter()
            .copied()
            .filter(|v| ring_b.contains(v))
            .collect();
        common.sort_unstable();
        let mut expected = vec![c, d];
        expected.sort_unstable();
        if c == d || common != expected {
            return false;
        }
        let (Some(star_a), Some(star_b)) = (self.star(a), self.star(b)) else {
            return false;
        };
        let mut region: Vec<usize> = star_a.iter().chain(&star_b).copied().collect();
        region.sort_unstable();
        region.dedup();
        if region.iter().any(|&t| self.touched[t]) {
            return false;
        }
        // The outline of the region, with the triangles outside it.
        let mut boundary = vec![];
        let mut outside = vec![];
        for &t in &region {
            for k in 0..3 {
                let edge = (3 * t + k) as u32;
                let other = self.opp[edge as usize];
                if other == DEAD {
                    return false;
                }
                let ot = other as usize / 3;
                if region.binary_search(&ot).is_ok() {
                    continue;
                }
                if self.touched[ot] {
                    return false;
                }
                outside.push(ot);
                boundary.push((
                    self.corner(edge),
                    self.corner(next(edge)),
                    self.normal(self.m.t[ot]),
                ));
            }
        }
        let mid = {
            let (pa, pb) = (self.pos(a), self.pos(b));
            [
                (pa[0] + pb[0]) / 2.0,
                (pa[1] + pb[1]) / 2.0,
                (pa[2] + pb[2]) / 2.0,
            ]
        };
        let old: Vec<[u32; 3]> = region.iter().map(|&t| self.m.t[t]).collect();
        let new: Vec<[u32; 3]> = old
            .iter()
            .filter(|t| !(t.contains(&a) && t.contains(&b)))
            .map(|t| t.map(|v| if v == b { a } else { v }))
            .collect();
        let moved_boundary: Vec<(u32, u32, [f64; 3])> = boundary
            .iter()
            .map(|&(x, y, n)| (if x == b { a } else { x }, if y == b { a } else { y }, n))
            .collect();
        let before = self.local_folds(&old, &boundary);
        // Where the merged vertex goes: the projected midpoint, else either end.
        let mut places = vec![];
        if let Some(q) = project(mid) {
            places.push(q);
        }
        places.extend([self.pos(a), self.pos(b)]);
        let mut best: Option<(usize, [f32; 3])> = None;
        for place in places {
            let at = place.map(|x| x as f32);
            if at.iter().any(|x| !x.is_finite()) {
                continue;
            }
            let normal_at = |t: [u32; 3]| {
                let [p0, p1, p2] = t.map(|v| {
                    if v == a {
                        at.map(f64::from)
                    } else {
                        self.pos(v)
                    }
                });
                cross(sub(p1, p0), sub(p2, p0))
            };
            let ns: Vec<[f64; 3]> = new.iter().map(|&t| normal_at(t)).collect();
            if ns.iter().any(|n| dot(*n, *n) <= self.min_area2) {
                continue;
            }
            let mut f = 0;
            for i in 0..new.len() {
                for j in i + 1..new.len() {
                    if shares_edge(new[i], new[j]) && self.folded(ns[i], ns[j]) {
                        f += 1;
                    }
                }
            }
            for &(x, y, outer) in &moved_boundary {
                for (t, n) in new.iter().zip(&ns) {
                    if has_directed(*t, x, y) && self.folded(*n, outer) {
                        f += 1;
                    }
                }
            }
            if best.is_none_or(|(g, _)| f < g) {
                best = Some((f, at));
            }
        }
        let Some((after, at)) = best else {
            return false;
        };
        // A collapse may leave one fold more than it found: it deletes a vertex, so the stage still
        // ends (removals and collapses lower the vertex count, flips and moves the fold count),
        // and the folds it leaves sit on fresh triangles the next round can flip or remove.
        if after > before + 1 || before == 0 {
            return false;
        }
        for &t in &region {
            self.touched[t] = true;
            self.dead[t] = true;
        }
        for &t in &outside {
            self.touched[t] = true;
        }
        self.new_tris.extend(new);
        self.moves.push((a, at));
        true
    }

    /// Flip the folded edge `h` when that lowers the local folds.
    fn try_flip(&mut self, h: u32) -> bool {
        let o = self.opp[h as usize];
        if o == DEAD {
            return false;
        }
        let (t1, t2) = (h as usize / 3, o as usize / 3);
        if t1 == t2 || self.touched[t1] || self.touched[t2] {
            return false;
        }
        let (a, b) = (self.corner(h), self.corner(next(h)));
        let c = self.corner(prev(h));
        let d = self.corner(prev(o));
        if c == d || self.has_edge(c, d) {
            return false;
        }
        // The outline: a -> d, d -> b (from t2) and b -> c, c -> a (from t1).
        let mut boundary = vec![];
        for edge in [next(o), prev(o), next(h), prev(h)] {
            let other = self.opp[edge as usize];
            if other == DEAD || self.touched[other as usize / 3] {
                return false;
            }
            let outer = self.m.t[other as usize / 3];
            boundary.push((
                self.corner(edge),
                self.corner(next(edge)),
                self.normal(outer),
            ));
        }
        let old = [self.m.t[t1], self.m.t[t2]];
        let new = [[a, d, c], [d, b, c]];
        if self.degenerate(&new) {
            return false;
        }
        let before = self.local_folds(&old, &boundary);
        let after = self.local_folds(&new, &boundary);
        if after >= before {
            return false;
        }
        for edge in [next(o), prev(o), next(h), prev(h)] {
            self.touched[self.opp[edge as usize] as usize / 3] = true;
        }
        self.touched[t1] = true;
        self.touched[t2] = true;
        self.dead[t1] = true;
        self.dead[t2] = true;
        self.new_tris.extend(new);
        true
    }
}

fn shares_edge(a: [u32; 3], b: [u32; 3]) -> bool {
    a.iter().filter(|v| b.contains(v)).count() >= 2
}

fn has_directed(t: [u32; 3], a: u32, b: u32) -> bool {
    (0..3).any(|k| t[k] == a && t[(k + 1) % 3] == b)
}

/// One round over the mesh; returns (vertices removed, flips, moves).
fn round(
    m: &mut TriMesh,
    opp: &[u32],
    angle_deg: f64,
    min_area: f64,
    project: Option<&mut Project<'_>>,
) -> (usize, usize, usize, usize) {
    let mut w = Work::new(m, opp, angle_deg, min_area);
    if project.is_some() {
        w.max_ring = MAX_RING;
    }
    // Vertices of folded triangles, in index order: candidates for removal and moves.
    let mut folded_tri = vec![false; m.t.len()];
    let mut folded_edges = vec![];
    for (h, &o) in opp.iter().enumerate() {
        if o == DEAD || (o as usize) < h {
            continue;
        }
        let (t1, t2) = (h / 3, o as usize / 3);
        if w.folded(w.normal(m.t[t1]), w.normal(m.t[t2])) {
            folded_tri[t1] = true;
            folded_tri[t2] = true;
            folded_edges.push(h as u32);
        }
    }
    let mut candidates: Vec<u32> = folded_tri
        .iter()
        .enumerate()
        .filter(|&(_, &f)| f)
        .flat_map(|(t, _)| m.t[t])
        .collect();
    candidates.sort_unstable();
    candidates.dedup();
    let mut removed = 0;
    for &v in &candidates {
        let valence = w.start[v as usize + 1] - w.start[v as usize];
        if (3..=w.max_ring as u32).contains(&valence) && w.try_remove(v) {
            removed += 1;
        }
    }
    let mut flips = 0;
    for &h in &folded_edges {
        if w.try_flip(h) {
            flips += 1;
        }
    }
    let mut moves = 0;
    let mut collapses = 0;
    if let Some(project) = project {
        for &h in &folded_edges {
            if w.try_collapse(h, project) {
                collapses += 1;
            }
        }
        for &v in &candidates {
            if w.try_move(v, project) {
                moves += 1;
            }
        }
    }
    let Work {
        dead,
        new_tris,
        moves: moved,
        ..
    } = w;
    let mut t: Vec<[u32; 3]> =
        m.t.iter()
            .zip(&dead)
            .filter(|(_, d)| !**d)
            .map(|(t, _)| *t)
            .collect();
    t.extend(new_tris);
    m.t = t;
    for (v, at) in moved {
        m.v[v as usize] = at;
    }
    (removed, flips, moves, collapses)
}

/// Remove folds in up to `max_rounds` rounds. `edges` recomputes the pairing after each round;
/// the pass stops when a round changes nothing. Removed vertices stay in `m.v` unused; the caller
/// compacts.
pub fn unfold(
    m: &mut TriMesh,
    edges: impl Fn(&TriMesh) -> Vec<u32>,
    angle_deg: f64,
    min_area: f64,
    max_rounds: usize,
    project: &mut Project<'_>,
) -> UnfoldStats {
    let mut stats = UnfoldStats::default();
    // Removals of small rings and flips first, until they find nothing; then the fallback rounds,
    // which also fill rings of up to MAX_RING and move vertices, for the folds the first two
    // cannot reach. Doing those earlier takes away the better removals that would have been found
    // later (measured: more folds left on a sphere, and thinner triangles).
    let mut moving = false;
    for _ in 0..max_rounds {
        let opp = edges(m);
        let (removed, flips, moves, collapses) = round(
            m,
            &opp,
            angle_deg,
            min_area,
            if moving { Some(&mut *project) } else { None },
        );
        stats.rounds += 1;
        stats.vertices_removed += removed;
        stats.flips += flips;
        stats.moves += moves;
        stats.collapses += collapses;
        if removed == 0 && flips == 0 && moves == 0 && collapses == 0 {
            if moving {
                break;
            }
            moving = true;
        }
    }
    stats
}
