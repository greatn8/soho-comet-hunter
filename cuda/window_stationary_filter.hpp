#pragma once

// Pure C++ preprocessing, independent of CUDA: remove near-fixed image
// detections observed on most frames in a short or long SOHO input window.
// Each spatial bin is counted at most once per input frame. Suppressing
// persistent coordinates BEFORE GPU seed linking prevents the tracker from
// stitching stationary stars/artifacts into fictitious moving trajectories.
//
// This is deliberately opt-in while sensitivity to known comets is tested.
// A slow real comet could otherwise be removed if it remains within one
// small spatial bin for most of the window.
#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <stdexcept>
#include <unordered_map>
#include <unordered_set>
#include <vector>

namespace comet {

template <class DetectionT>
std::size_t removeWindowStationaryPoints(
    std::vector<std::vector<DetectionT>>& frames,
    int spatialBin = 2,
    double minFrameFraction = 0.80
) {
    if (spatialBin < 1 || !std::isfinite(minFrameFraction) ||
        minFrameFraction < 0.5 || minFrameFraction > 1.0) {
        throw std::invalid_argument("Invalid within-window stationary-filter parameters");
    }
    if (frames.size() < 8) {
        return 0;
    }

    auto key = [spatialBin](const DetectionT& point) -> std::uint64_t {
        auto x = static_cast<std::int64_t>(std::floor(point.x / spatialBin));
        auto y = static_cast<std::int64_t>(std::floor(point.y / spatialBin));
        return (static_cast<std::uint64_t>(static_cast<std::uint32_t>(x)) << 32)
             | static_cast<std::uint32_t>(y);
    };

    std::unordered_map<std::uint64_t, int> framesPerBin;
    for (const auto& frame : frames) {
        std::unordered_set<std::uint64_t> seenInFrame;
        seenInFrame.reserve(frame.size());
        for (const auto& point : frame) {
            seenInFrame.insert(key(point));
        }
        for (const auto& location : seenInFrame) {
            ++framesPerBin[location];
        }
    }

    const int minCount = std::max(
        5, static_cast<int>(std::ceil(minFrameFraction * frames.size()))
    );
    std::size_t removed = 0;
    for (auto& frame : frames) {
        auto oldSize = frame.size();
        frame.erase(
            std::remove_if(frame.begin(), frame.end(), [&](const DetectionT& point) {
                auto it = framesPerBin.find(key(point));
                return it != framesPerBin.end() && it->second >= minCount;
            }), frame.end()
        );
        removed += oldSize - frame.size();
    }
    return removed;
}

} // namespace comet
