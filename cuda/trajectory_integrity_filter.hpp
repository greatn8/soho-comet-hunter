#pragma once

// Offline-testable source-level guards against CUDA trajectory hallucinations.
// All coordinates are in ORIGINAL image pixels. No fixed quadrant or dark-
// stripe mask is used. Reject incoherent fitted endpoints, then (optionally)
// compare trajectory support to translated paths through the same images.
//
// Both guards are opt-in until the negative / moving-source CUDA controls
// are rerun on Bourbaki, including native 512-pixel LASCO input.
#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <ctime>
#include <limits>
#include <unordered_set>
#include <vector>

namespace comet {

template <typename TrackT, typename ImageT>
bool consistentEndpointVelocity(
    const TrackT& track,
    const std::vector<ImageT>& images,
    double minimumPixelTolerance = 12.0,
    double tolerancePerHour = 1.5
) {
    if (track.points.size() < 2 ||
        !std::isfinite(track.vx) || !std::isfinite(track.vy)) {
        return false;
    }
    const auto& first = track.points.front();
    const auto& last = track.points.back();
    if (first.frame < 0 || last.frame < 0 ||
        static_cast<std::size_t>(last.frame) >= images.size() ||
        static_cast<std::size_t>(first.frame) >= images.size()) {
        return false;
    }
    double hours = std::difftime(images[last.frame].timestamp,
                                images[first.frame].timestamp) / 3600.0;
    if (!std::isfinite(hours) || hours <= 0) {
        return false;
    }
    double distance = std::hypot(
        static_cast<double>(last.x - first.x) - track.vx * hours,
        static_cast<double>(last.y - first.y) - track.vy * hours);
    return std::isfinite(distance) &&
        distance <= std::max(minimumPixelTolerance, hours * tolerancePerHour);
}

using FrameOccupancy = std::vector<std::unordered_set<std::uint64_t>>;

inline std::uint64_t packedPixel(int x, int y) {
    return (static_cast<std::uint64_t>(static_cast<std::uint32_t>(y)) << 32) |
           static_cast<std::uint32_t>(x);
}

template <typename DetectionT>
FrameOccupancy makeFrameOccupancy(
    const std::vector<std::vector<DetectionT>>& detections
) {
    FrameOccupancy frames;
    frames.reserve(detections.size());
    for (const auto& detectionsInFrame : detections) {
        std::unordered_set<std::uint64_t> occupied;
        occupied.reserve(detectionsInFrame.size());
        for (const auto& p : detectionsInFrame) {
            occupied.insert(packedPixel(
                static_cast<int>(std::lround(p.x)),
                static_cast<int>(std::lround(p.y))
            ));
        }
        frames.push_back(std::move(occupied));
    }
    return frames;
}

inline bool hasLocalHit(
    const std::unordered_set<std::uint64_t>& positions,
    double x, double y, int width, int height, int radius
) {
    if (!std::isfinite(x) || !std::isfinite(y)) {
        return false;
    }
    const int px = static_cast<int>(std::lround(x));
    const int py = static_cast<int>(std::lround(y));
    for (int dy = -radius; dy <= radius; ++dy) {
        for (int dx = -radius; dx <= radius; ++dx) {
            const int xx = px + dx, yy = py + dy;
            if (xx < 0 || yy < 0 || xx >= width || yy >= height) {
                continue;
            }
            if (positions.count(packedPixel(xx, yy)) != 0) {
                return true;
            }
        }
    }
    return false;
}

struct PathNullEvidence {
    int realHits = 0;
    int imageFrames = 0;
    int comparisonPaths = 0;
    int upperQuartileNullHits = 0;
    bool evaluable = false;
    bool supported = true; // Fail open if too close to edge / little context.
};

template <typename TrackT, typename ImageT>
PathNullEvidence measurePathNullEvidence(
    const TrackT& track,
    const std::vector<ImageT>& images,
    const FrameOccupancy& occupied,
    int width, int height, int tolerance = 2
) {
    PathNullEvidence out;
    if (track.points.size() < 5 || images.size() != occupied.size() ||
        tolerance < 0 || tolerance > 4 ||
        !consistentEndpointVelocity(track, images)) {
        return out;
    }
    const int firstFrame = track.points.front().frame;
    const int lastFrame = track.points.back().frame;
    if (lastFrame <= firstFrame || lastFrame >= static_cast<int>(images.size())) {
        return out;
    }

    const std::time_t t0 = images[firstFrame].timestamp;
    // Fit x/y intercept from ALL measured detections, not just a single
    // possibly noisy endpoint. The velocities came from least squares.
    double bX = 0, bY = 0;
    for (const auto& p : track.points) {
        const double h = std::difftime(images[p.frame].timestamp, t0) / 3600.;
        bX += static_cast<double>(p.x) - track.vx * h;
        bY += static_cast<double>(p.y) - track.vy * h;
    }
    bX /= track.points.size();
    bY /= track.points.size();

    struct Offset { int x; int y; };
    constexpr Offset NULL_OFFSETS[] = {
        {-6,0},{6,0},{0,-6},{0,6},
        {-12,0},{12,0},{0,-12},{0,12},
        {-12,-12},{-12,12},{12,-12},{12,12}
    };
    constexpr int nOffsets = sizeof(NULL_OFFSETS) / sizeof(NULL_OFFSETS[0]);
    int nullHits[nOffsets] = {};
    bool valid[nOffsets];
    std::fill(valid, valid+nOffsets, true);

    for (int f = firstFrame; f <= lastFrame; ++f) {
        const double h = std::difftime(images[f].timestamp, t0) / 3600.0;
        const double x = bX + track.vx * h, y = bY + track.vy * h;
        if (!std::isfinite(x) || !std::isfinite(y)) {
            return out;
        }
        ++out.imageFrames;
        if (hasLocalHit(occupied[f], x, y, width, height, tolerance)) {
            ++out.realHits;
        }
        for (int j=0; j<nOffsets; ++j) {
            const double sx = x+NULL_OFFSETS[j].x;
            const double sy = y+NULL_OFFSETS[j].y;
            if (sx < tolerance || sy < tolerance ||
                sx >= width-tolerance || sy >= height-tolerance) {
                valid[j] = false;
            } else if (hasLocalHit(occupied[f],sx,sy,width,height,tolerance)) {
                ++nullHits[j];
            }
        }
    }
    std::vector<int> controls;
    for (int j=0; j<nOffsets; ++j) {
        if (valid[j]) controls.push_back(nullHits[j]);
    }
    out.comparisonPaths = static_cast<int>(controls.size());
    if (out.imageFrames < 5 || out.comparisonPaths < 4) {
        return out;
    }
    std::sort(controls.begin(), controls.end());
    out.upperQuartileNullHits = controls[(controls.size()*3)/4];
    out.evaluable = true;

    // Coherent moving objects must outshine nearby translated straight
    // trajectories. Background-stripe texture can support many nearby
    // synthetic "tracks", which should fail this contrast.
    const int requiredLift = std::max(
        3, static_cast<int>(std::ceil(out.imageFrames * 0.20)));
    out.supported =
        out.realHits >= 5 &&
        out.realHits >= out.upperQuartileNullHits + requiredLift;
    return out;
}

} // namespace comet
