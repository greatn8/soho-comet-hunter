// Standalone C++17 regression test (no GPU required).
// g++ -std=c++17 -O2 -Wall -Wextra -pedantic -I cuda tests/trajectory_integrity_filter_test.cpp -o /tmp/soho_track_test && /tmp/soho_track_test
#include "trajectory_integrity_filter.hpp"
#include <cassert>
#include <cmath>
#include <ctime>
#include <iostream>
#include <vector>

struct TestImage { std::time_t timestamp; };
struct TestDetection { int frame; float x, y, strength; };
struct TestTrack {
    std::vector<TestDetection> points;
    float vx, vy;
};
int main() {
    std::vector<TestImage> images;
    std::vector<std::vector<TestDetection>> detections;
    TestTrack moving{{}, 3.0f, 1.0f};
    TestTrack inconsistent{{}, 3.0f, 1.0f};
    for (int i=0;i<24;++i) {
        images.push_back({static_cast<std::time_t>(1000000000+i*600)});
        float x=80.0f+i*0.5f,y=100.0f+i/6.0f;
        moving.points.push_back({i,x,y,10.0f});
        inconsistent.points.push_back({i,280.0f-i*0.5f,y,10.0f});
        detections.push_back({{i,x,y,10.0f}});
    }
    assert(comet::consistentEndpointVelocity(moving,images));
    assert(!comet::consistentEndpointVelocity(inconsistent,images));
    auto occupied=comet::makeFrameOccupancy(detections);
    auto real=comet::measurePathNullEvidence(moving,images,occupied,512,512);
    assert(real.evaluable);
    assert(real.supported);
    assert(real.realHits >= 22);

    // Adversarial moving-looking stripe: a dense stable band supports
    // the hypothesized path AND nearby translated paths equally well.
    // It must not be allowed to occupy the realtime candidate cap.
    std::vector<std::vector<TestDetection>> stripe;
    for (int i=0;i<24;++i) {
        std::vector<TestDetection> row;
        for (int y=85;y<=130;++y)
            for (int x=65;x<=115;++x)
                row.push_back({i,static_cast<float>(x),static_cast<float>(y),5});
        stripe.push_back(std::move(row));
    }
    auto stripes=comet::makeFrameOccupancy(stripe);
    auto artifact=comet::measurePathNullEvidence(moving,images,stripes,512,512);
    assert(artifact.evaluable);
    assert(!artifact.supported);
    assert(artifact.upperQuartileNullHits >= 20);

    // A candidate near the image edge must not be silently lost just
    // because there are insufficient spatially shifted null controls.
    TestTrack edge{{},3.0f,1.0f};
    for(int i=0;i<24;++i)
        edge.points.push_back({i,1.0f+i*0.5f,2.0f+i/6.0f,10.0f});
    auto edgeCase=comet::measurePathNullEvidence(edge,images,occupied,512,512);
    assert(!edgeCase.evaluable || edgeCase.supported);

    std::cout << "PASS: coherent moving signal retained; inconsistent endpoints and stripe null paths rejected\n";
}
