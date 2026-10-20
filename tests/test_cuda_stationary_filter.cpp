#include "window_stationary_filter.hpp"
#include <cassert>
#include <cmath>
#include <iostream>
#include <stdexcept>
#include <vector>

struct Point { int frame; float x; float y; float strength; };

int main() {
    // Identical input images => every detected source remains stationary.
    std::vector<std::vector<Point>> staticFrames(23);
    for (int frame=0; frame<23; ++frame) {
        staticFrames[frame] = {{frame,100,200,10}, {frame,250,400,25}};
    }
    const auto removed = comet::removeWindowStationaryPoints(staticFrames);
    assert(removed==46);
    for (const auto& row : staticFrames) assert(row.empty());

    // A compact moving source crossing bins survives.
    std::vector<std::vector<Point>> movingFrames(23);
    for (int frame=0; frame<23; ++frame) {
        movingFrames[frame] = {{frame,float(100+3*frame),200,10}};
    }
    assert(comet::removeWindowStationaryPoints(movingFrames)==0);
    for (const auto& row : movingFrames) assert(row.size()==1);

    // Persistent star present in 20/23 frames removed, transient preserved.
    std::vector<std::vector<Point>> mixed(23);
    for (int frame=0; frame<23; ++frame) {
        if (frame<20) mixed[frame].push_back({frame,120,120,9});
        if (frame>=8 && frame<13)
            mixed[frame].push_back({frame,float(300+3*frame),250,7});
    }
    assert(comet::removeWindowStationaryPoints(mixed)==20);
    int survivors=0;
    for (const auto& row : mixed) survivors += int(row.size());
    assert(survivors==5);

    // Do not eliminate points in very short sequences.
    std::vector<std::vector<Point>> shortRun(5);
    for (int frame=0; frame<5; ++frame)
        shortRun[frame].push_back({frame,100,100,10});
    assert(comet::removeWindowStationaryPoints(shortRun)==0);

    bool rejected=false;
    try { comet::removeWindowStationaryPoints(movingFrames,2,0.1); }
    catch (const std::invalid_argument&) { rejected=true; }
    assert(rejected);

    std::cout << "Within-window stationary control tests passed.\n";
    return 0;
}
