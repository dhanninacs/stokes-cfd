// Stokes native kernels. Mirrors stokes/native/fallback.py; keep the two in sync.
// Rules: buffers in, no per-element allocation, release the GIL above a millisecond.

#include <nanobind/nanobind.h>
#include <nanobind/ndarray.h>

#include <cmath>
#include <cstdint>
#include <vector>

namespace nb = nanobind;

using Float2 = nb::ndarray<double, nb::shape<-1, 3>, nb::c_contig, nb::device::cpu>;
using Int1 = nb::ndarray<int64_t, nb::shape<-1>, nb::c_contig, nb::device::cpu>;

// Triangles reachable from the seeds across edges whose dihedral angle is below angle_deg.
static nb::ndarray<nb::numpy, int64_t> grow_by_angle(Float2 normals, Int1 offsets, Int1 neighbours,
                                                      Int1 seeds, double angle_deg) {
    const size_t count = normals.shape(0);
    const double cos_limit = std::cos(angle_deg * M_PI / 180.0);
    std::vector<uint8_t> visited(count, 0);
    std::vector<int64_t> queue;
    queue.reserve(count);
    for (size_t i = 0; i < seeds.shape(0); ++i) {
        const int64_t s = seeds(i);
        if (s >= 0 && static_cast<size_t>(s) < count && !visited[s]) {
            visited[s] = 1;
            queue.push_back(s);
        }
    }
    {
        nb::gil_scoped_release release;
        size_t head = 0;
        while (head < queue.size()) {
            const int64_t t = queue[head++];
            const double* nt = &normals(t, 0);
            for (int64_t k = offsets(t); k < offsets(t + 1); ++k) {
                const int64_t other = neighbours(k);
                if (visited[other]) continue;
                const double* no = &normals(other, 0);
                const double dot = nt[0] * no[0] + nt[1] * no[1] + nt[2] * no[2];
                if (dot >= cos_limit) {
                    visited[other] = 1;
                    queue.push_back(other);
                }
            }
        }
    }
    std::vector<int64_t> result;
    for (size_t i = 0; i < count; ++i)
        if (visited[i]) result.push_back(static_cast<int64_t>(i));
    int64_t* data = new int64_t[result.size()];
    std::copy(result.begin(), result.end(), data);
    nb::capsule owner(data, [](void* p) noexcept { delete[] static_cast<int64_t*>(p); });
    return nb::ndarray<nb::numpy, int64_t>(data, {result.size()}, owner);
}

NB_MODULE(stokes_native, m) {
    m.doc() = "Stokes native kernels (nanobind)";
    m.def("grow_by_angle", &grow_by_angle, nb::arg("normals"), nb::arg("offsets"),
          nb::arg("neighbours"), nb::arg("seeds"), nb::arg("angle_deg"));
}
