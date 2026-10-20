#pragma once

// Opt-in spatially balanced local-maximum selection for LASCO C3/C2.
// Strong nearby stars and a bright stripe can crowd faint moving sources
// out of a global brightest-N detector budget. Reserve 75% of the same
// budget for independent image tiles, then fill the rest globally.
//
// This operates AFTER the standard local-peak/threshold detector and
// BEFORE stationary suppression/seed search. It never fabricates a
// detection or accepts a pixel below the existing pass threshold.
//
// No coordinates of known comets are used. The algorithm is strictly
// image-local, deterministic, and does not mask any quadrant.
#include <algorithm>
#include <cmath>
#include <cstddef>
#include <numeric>
#include <stdexcept>
#include <vector>

namespace comet {

template <typename PointT>
std::vector<PointT> selectSpatiallyBalanced(
    std::vector<PointT> points,
    int width,
    int height,
    int maximumPoints,
    int tilesPerAxis = 16
) {
    if (width <= 0 || height <= 0 || maximumPoints <= 0 ||
        tilesPerAxis < 2 || tilesPerAxis > 128) {
        throw std::invalid_argument("Invalid spatially balanced point selection arguments");
    }
    // Sort with the exact same comparator as the original global selector,
    // including y/x tie-breaks to eliminate GPU atomic ordering variations.
    std::sort(points.begin(), points.end(),
        [](const PointT& a, const PointT& b) {
            if (a.strength != b.strength) return a.strength > b.strength;
            if (a.y != b.y) return a.y < b.y;
            return a.x < b.x;
        });
    if (points.size() <= static_cast<std::size_t>(maximumPoints)) {
        return points;
    }

    const std::size_t cap = static_cast<std::size_t>(maximumPoints);
    const int bins = tilesPerAxis * tilesPerAxis;
    const int reserved = maximumPoints * 3 / 4;
    const int quota = std::max(1, reserved / bins);
    std::vector<int> tileCounts(bins, 0);
    std::vector<unsigned char> chosen(points.size(), 0);
    int selected = 0;

    for (std::size_t i = 0; i < points.size() && selected < reserved; ++i) {
        const PointT& p = points[i];
        if (!std::isfinite(static_cast<double>(p.x)) ||
            !std::isfinite(static_cast<double>(p.y)) ||
            p.x < 0 || p.y < 0 || p.x >= width || p.y >= height) {
            continue;
        }
        const int tx = std::min(tilesPerAxis - 1,
                                static_cast<int>(p.x * tilesPerAxis / width));
        const int ty = std::min(tilesPerAxis - 1,
                                static_cast<int>(p.y * tilesPerAxis / height));
        const int index = ty * tilesPerAxis + tx;
        if (tileCounts[index] >= quota) continue;
        ++tileCounts[index];
        chosen[i] = 1;
        ++selected;
    }

    // Use remaining slots for globally strongest sources, preserving the
    // original detector's brightness preference for all unused capacity.
    for (std::size_t i = 0; i < points.size() && selected < maximumPoints; ++i) {
        if (chosen[i]) continue;
        const PointT& p = points[i];
        if (!std::isfinite(static_cast<double>(p.x)) ||
            !std::isfinite(static_cast<double>(p.y)) ||
            p.x < 0 || p.y < 0 || p.x >= width || p.y >= height) {
            continue;
        }
        chosen[i] = 1;
        ++selected;
    }

    std::vector<PointT> result;
    result.reserve(cap);
    // Re-establish global strength order so the trajectory engine's existing
    // seed search receives the same ordering convention as before.
    for (std::size_t i = 0; i < points.size() && result.size() < cap; ++i) {
        if (chosen[i]) result.push_back(std::move(points[i]));
    }
    return result;
}

} // namespace comet
