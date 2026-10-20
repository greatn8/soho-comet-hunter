
#include <cuda_runtime.h>
#include <nvjpeg.h>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <regex>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

#include "window_stationary_filter.hpp"
#include "trajectory_integrity_filter.hpp"
#include "spatial_peak_selector.hpp"

#include <sys/types.h>
#include <unistd.h>

namespace fs = std::filesystem;

#define CUDA_CHECK(call)                                                        \
    do {                                                                        \
        cudaError_t err__ = (call);                                             \
        if (err__ != cudaSuccess) {                                             \
            std::ostringstream oss__;                                           \
            oss__ << "CUDA error: " << cudaGetErrorString(err__)                \
                  << " at " << __FILE__ << ":" << __LINE__;                     \
            throw std::runtime_error(oss__.str());                              \
        }                                                                       \
    } while (0)

#define NVJPEG_CHECK(call)                                                      \
    do {                                                                        \
        nvjpegStatus_t st__ = (call);                                           \
        if (st__ != NVJPEG_STATUS_SUCCESS) {                                    \
            std::ostringstream oss__;                                           \
            oss__ << "nvJPEG error code " << static_cast<int>(st__)             \
                  << " at " << __FILE__ << ":" << __LINE__;                     \
            throw std::runtime_error(oss__.str());                              \
        }                                                                       \
    } while (0)

struct ImageMeta {
    std::string path;
    std::string name;
    std::time_t timestamp = 0;
    int width = 0;
    int height = 0;
};

struct Detection {
    int frame = 0;
    float x = 0.0f;
    float y = 0.0f;
    float strength = 0.0f;
};

struct DeviceDetection {
    int x;
    int y;
    float strength;
};

struct GpuSeed {
    int startFrame;
    int seedStep;
    int x0;
    int y0;
    float vx;
    float vy;
    int hits;
    float predictionRms;
};

struct Track {
    std::vector<Detection> points;
    float vx = 0.0f;
    float vy = 0.0f;
    float speed = 0.0f;
    float rms = 0.0f;
    float brightnessCv = 0.0f;
    float sunwardRate = 0.0f;
    float score = 0.0f;
    std::string priority;
    std::string passName;
};

struct PassParams {
    std::string name;
    float threshold;
    int maxPoints;
    int searchWindow;
    int tolerance;
    int seedStepMax;
    int artifactMinDays;
    float maxComponentSpeed;
};

struct Args {
    std::string camera = "c3";
    std::string resolution = "auto";
    std::string inputDir;

    int budgetMinutes = 60;
    int minFrames = 5;

    int edgeMask = 24;
    float centerMaskFraction = 0.09f;
    int artifactBin = 3;

    // OFF by default until the historical known-comet recovery benchmark
    // confirms sensitivity. Never silently change the production detector.
    bool withinWindowStationaryFilter = false;
    float stationaryMinFrameFraction = 0.80f;
    int stationaryBin = 2;
    bool trajectoryIntegrityFilter = false;
    bool trajectoryNullControl = false;
    bool spatialBalancedPeaks = false;

    int maxCandidates = 100;
    int rawSeedKeep = 2500;
    int seedsPerStartKeep = 40;
    int gpuSeedCapacity = 12000;

    int border = 8;
    int bgRadius = 4;

    float maxSeedGapMinutes = 90.0f;
    bool once = true;
};

class RamWorkspace {
public:
    RamWorkspace() {
        std::ostringstream name;
        name << "/dev/shm/comet_hunter_v6_" << getpid();
        root_ = name.str();

        std::error_code ec;
        fs::remove_all(root_, ec);
        fs::create_directories(root_);
    }

    ~RamWorkspace() {
        std::error_code ec;
        fs::remove_all(root_, ec);
    }

    const fs::path& root() const { return root_; }

private:
    fs::path root_;
};

static std::string shellQuote(const std::string& value) {
    std::string out = "'";
    for (char c : value) {
        if (c == '\'') out += "'\\''";
        else out += c;
    }
    out += "'";
    return out;
}

static bool commandExists(const std::string& command) {
    std::string cmd = "command -v " + command + " >/dev/null 2>&1";
    return std::system(cmd.c_str()) == 0;
}

static std::string lowerCopy(std::string text) {
    std::transform(
        text.begin(),
        text.end(),
        text.begin(),
        [](unsigned char c) {
            return static_cast<char>(std::tolower(c));
        }
    );
    return text;
}

static std::time_t parseTimestamp(const std::string& name) {
    static const std::regex r1(R"((\d{8})[_-](\d{4,6}))");
    static const std::regex r2(R"((\d{8})(\d{4,6}))");
    std::smatch match;

    auto makeTime = [](const std::string& date, const std::string& time) {
        if (date.size() != 8 || (time.size() != 4 && time.size() != 6)) {
            return static_cast<std::time_t>(0);
        }

        std::tm tm{};
        tm.tm_year = std::stoi(date.substr(0, 4)) - 1900;
        tm.tm_mon = std::stoi(date.substr(4, 2)) - 1;
        tm.tm_mday = std::stoi(date.substr(6, 2));
        tm.tm_hour = std::stoi(time.substr(0, 2));
        tm.tm_min = std::stoi(time.substr(2, 2));
        tm.tm_sec = time.size() == 6 ? std::stoi(time.substr(4, 2)) : 0;
        tm.tm_isdst = 0;

        return timegm(&tm);
    };

    if (std::regex_search(name, match, r1)) {
        return makeTime(match[1], match[2]);
    }

    if (std::regex_search(name, match, r2)) {
        return makeTime(match[1], match[2]);
    }

    return 0;
}

static int utcDateKey(std::time_t timestamp) {
    std::tm tm{};
    gmtime_r(&timestamp, &tm);

    return
        (tm.tm_year + 1900) * 10000
        + (tm.tm_mon + 1) * 100
        + tm.tm_mday;
}

static std::vector<unsigned char> readFile(const std::string& path) {
    std::ifstream file(path, std::ios::binary | std::ios::ate);

    if (!file) {
        throw std::runtime_error("Could not open JPEG: " + path);
    }

    std::streamsize size = file.tellg();

    if (size <= 0) {
        throw std::runtime_error("Empty JPEG: " + path);
    }

    file.seekg(0, std::ios::beg);

    std::vector<unsigned char> bytes(
        static_cast<size_t>(size)
    );

    if (!file.read(
        reinterpret_cast<char*>(bytes.data()),
        size
    )) {
        throw std::runtime_error(
            "Could not read JPEG: " + path
        );
    }

    return bytes;
}

static std::string sourceUrl(const std::string& camera) {
    return camera == "c2"
        ? "https://soho.nascom.nasa.gov/data/LATEST/latest-lascoC2.zip"
        : "https://soho.nascom.nasa.gov/data/LATEST/latest-lascoC3.zip";
}

static void fetchAndExtract(
    const Args& args,
    const RamWorkspace& workspace
) {
    if (!commandExists("curl")) {
        throw std::runtime_error("curl command not found");
    }

    if (!commandExists("unzip")) {
        throw std::runtime_error("unzip command not found");
    }

    fs::path zipPath =
        workspace.root() / "latest.zip";

    fs::path imageDir =
        workspace.root() / "images";

    std::error_code ec;
    fs::remove_all(imageDir, ec);
    fs::create_directories(imageDir);

    std::string url =
        sourceUrl(args.camera);

    std::cout
        << "Downloading current LASCO "
        << args.camera
        << " bundle into /dev/shm...\n";

    std::string curlCommand =
        "curl -L --fail --silent --show-error "
        "--connect-timeout 30 --max-time 180 "
        "-A 'CometHunterDeep/6.0' "
        "-o " + shellQuote(zipPath.string()) + " "
        + shellQuote(url);

    int rc =
        std::system(curlCommand.c_str());

    if (rc != 0) {
        throw std::runtime_error(
            "curl failed with exit status "
            + std::to_string(rc)
        );
    }

    std::uintmax_t zipSize =
        fs::file_size(zipPath);

    std::cout
        << "RAM ZIP size: "
        << std::fixed
        << std::setprecision(2)
        << zipSize / (1024.0 * 1024.0)
        << " MiB\n";

    std::string unzipCommand =
        "unzip -q -o "
        + shellQuote(zipPath.string())
        + " -d "
        + shellQuote(imageDir.string());

    rc =
        std::system(unzipCommand.c_str());

    if (rc != 0) {
        throw std::runtime_error(
            "unzip failed with exit status "
            + std::to_string(rc)
        );
    }

    fs::remove(zipPath, ec);
}

static std::vector<ImageMeta> collectImages(
    const Args& args,
    const RamWorkspace& workspace
) {
    fs::path imageDir =
        workspace.root() / "images";

    struct Entry {
        fs::path path;
        std::string name;
        std::time_t timestamp;
    };

    std::vector<Entry> entries;

    for (
        const auto& item :
        fs::recursive_directory_iterator(imageDir)
    ) {
        if (!item.is_regular_file()) {
            continue;
        }

        std::string extension =
            lowerCopy(
                item.path().extension().string()
            );

        if (
            extension != ".jpg"
            &&
            extension != ".jpeg"
        ) {
            continue;
        }

        std::string name =
            item.path().filename().string();

        std::string lower =
            lowerCopy(name);

        std::string cameraToken =
            "_" + args.camera + "_";

        if (
            lower.find(cameraToken)
            == std::string::npos
        ) {
            continue;
        }

        entries.push_back({
            item.path(),
            name,
            parseTimestamp(name)
        });
    }

    if (entries.empty()) {
        throw std::runtime_error(
            "No matching LASCO JPEGs found"
        );
    }

    std::string selectedResolution =
        args.resolution;

    if (selectedResolution == "auto") {
        bool has1024 = false;
        bool has512 = false;

        for (const auto& item : entries) {
            std::string lower =
                lowerCopy(item.name);

            has1024 |=
                lower.find("_1024.")
                != std::string::npos;

            has512 |=
                lower.find("_512.")
                != std::string::npos;
        }

        if (has1024) {
            selectedResolution = "1024";
        }
        else if (has512) {
            selectedResolution = "512";
        }
        else {
            selectedResolution.clear();
        }
    }

    if (!selectedResolution.empty()) {
        std::string token =
            "_" + selectedResolution + ".";

        entries.erase(
            std::remove_if(
                entries.begin(),
                entries.end(),
                [&](const Entry& item) {
                    return
                        lowerCopy(item.name).find(token)
                        == std::string::npos;
                }
            ),
            entries.end()
        );
    }

    if (entries.empty()) {
        throw std::runtime_error(
            "No images matched resolution "
            + args.resolution
        );
    }

    std::sort(
        entries.begin(),
        entries.end(),
        [](const Entry& a, const Entry& b) {
            if (
                a.timestamp != 0
                &&
                b.timestamp != 0
                &&
                a.timestamp != b.timestamp
            ) {
                return
                    a.timestamp < b.timestamp;
            }

            return a.name < b.name;
        }
    );

    std::vector<ImageMeta> images;
    std::unordered_set<std::time_t>
        seenTimestamps;

    std::time_t fallback =
        946684800;

    for (
        size_t i = 0;
        i < entries.size();
        ++i
    ) {
        std::time_t timestamp =
            entries[i].timestamp != 0
            ? entries[i].timestamp
            : fallback
              + static_cast<std::time_t>(
                    i * 12 * 60
                );

        if (
            seenTimestamps.find(timestamp)
            != seenTimestamps.end()
        ) {
            continue;
        }

        seenTimestamps.insert(timestamp);

        ImageMeta meta;
        meta.path =
            entries[i].path.string();
        meta.name =
            entries[i].name;
        meta.timestamp =
            timestamp;

        images.push_back(
            std::move(meta)
        );
    }

    std::cout
        << "Selected resolution: "
        << (
            selectedResolution.empty()
            ? "unknown"
            : selectedResolution
        )
        << "\n";

    return images;
}

static std::vector<ImageMeta> collectImagesFromDir(
    const Args& args,
    const fs::path& imageDir
) {

    struct Entry {
        fs::path path;
        std::string name;
        std::time_t timestamp;
    };

    std::vector<Entry> entries;

    for (
        const auto& item :
        fs::recursive_directory_iterator(imageDir)
    ) {
        if (!item.is_regular_file()) {
            continue;
        }

        std::string extension =
            lowerCopy(
                item.path().extension().string()
            );

        if (
            extension != ".jpg"
            &&
            extension != ".jpeg"
        ) {
            continue;
        }

        std::string name =
            item.path().filename().string();

        std::string lower =
            lowerCopy(name);

        std::string cameraToken =
            "_" + args.camera + "_";

        if (
            lower.find(cameraToken)
            == std::string::npos
        ) {
            continue;
        }

        entries.push_back({
            item.path(),
            name,
            parseTimestamp(name)
        });
    }

    if (entries.empty()) {
        throw std::runtime_error(
            "No matching LASCO JPEGs found"
        );
    }

    std::string selectedResolution =
        args.resolution;

    if (selectedResolution == "auto") {
        bool has1024 = false;
        bool has512 = false;

        for (const auto& item : entries) {
            std::string lower =
                lowerCopy(item.name);

            has1024 |=
                lower.find("_1024.")
                != std::string::npos;

            has512 |=
                lower.find("_512.")
                != std::string::npos;
        }

        if (has1024) {
            selectedResolution = "1024";
        }
        else if (has512) {
            selectedResolution = "512";
        }
        else {
            selectedResolution.clear();
        }
    }

    if (!selectedResolution.empty()) {
        std::string token =
            "_" + selectedResolution + ".";

        entries.erase(
            std::remove_if(
                entries.begin(),
                entries.end(),
                [&](const Entry& item) {
                    return
                        lowerCopy(item.name).find(token)
                        == std::string::npos;
                }
            ),
            entries.end()
        );
    }

    if (entries.empty()) {
        throw std::runtime_error(
            "No images matched resolution "
            + args.resolution
        );
    }

    std::sort(
        entries.begin(),
        entries.end(),
        [](const Entry& a, const Entry& b) {
            if (
                a.timestamp != 0
                &&
                b.timestamp != 0
                &&
                a.timestamp != b.timestamp
            ) {
                return
                    a.timestamp < b.timestamp;
            }

            return a.name < b.name;
        }
    );

    std::vector<ImageMeta> images;
    std::unordered_set<std::time_t>
        seenTimestamps;

    std::time_t fallback =
        946684800;

    for (
        size_t i = 0;
        i < entries.size();
        ++i
    ) {
        std::time_t timestamp =
            entries[i].timestamp != 0
            ? entries[i].timestamp
            : fallback
              + static_cast<std::time_t>(
                    i * 12 * 60
                );

        if (
            seenTimestamps.find(timestamp)
            != seenTimestamps.end()
        ) {
            continue;
        }

        seenTimestamps.insert(timestamp);

        ImageMeta meta;
        meta.path =
            entries[i].path.string();
        meta.name =
            entries[i].name;
        meta.timestamp =
            timestamp;

        images.push_back(
            std::move(meta)
        );
    }

    std::cout
        << "Selected resolution: "
        << (
            selectedResolution.empty()
            ? "unknown"
            : selectedResolution
        )
        << "\n";

    return images;
}

__global__ void detectKernel(
    const unsigned char* image,
    int width,
    int height,
    int pitch,
    int border,
    int edgeMask,
    float centerX,
    float centerY,
    float centerRadius2,
    int bgRadius,
    float threshold,
    int rawCapacity,
    DeviceDetection* output,
    int* count
) {
    int x =
        blockIdx.x * blockDim.x
        + threadIdx.x;

    int y =
        blockIdx.y * blockDim.y
        + threadIdx.y;

    int effectiveBorder =
        max(border, edgeMask);

    if (
        x < effectiveBorder
        ||
        y < effectiveBorder
        ||
        x >= width - effectiveBorder
        ||
        y >= height - effectiveBorder
    ) {
        return;
    }

    float centerDx =
        static_cast<float>(x) - centerX;

    float centerDy =
        static_cast<float>(y) - centerY;

    if (
        centerDx * centerDx
        + centerDy * centerDy
        < centerRadius2
    ) {
        return;
    }

    auto pixel =
        [&](int px, int py)
        -> unsigned char {
            return
                image[py * pitch + px];
        };

    float center =
        static_cast<float>(
            pixel(x, y)
        );

    for (
        int dy = -1;
        dy <= 1;
        ++dy
    ) {
        for (
            int dx = -1;
            dx <= 1;
            ++dx
        ) {
            if (
                dx == 0
                &&
                dy == 0
            ) {
                continue;
            }

            if (
                static_cast<float>(
                    pixel(
                        x + dx,
                        y + dy
                    )
                )
                > center
            ) {
                return;
            }
        }
    }

    float sum = 0.0f;
    int samples = 0;

    for (
        int dy = -bgRadius;
        dy <= bgRadius;
        ++dy
    ) {
        for (
            int dx = -bgRadius;
            dx <= bgRadius;
            ++dx
        ) {
            if (
                abs(dx) <= 1
                &&
                abs(dy) <= 1
            ) {
                continue;
            }

            int px = x + dx;
            int py = y + dy;

            if (
                px < 0
                ||
                py < 0
                ||
                px >= width
                ||
                py >= height
            ) {
                continue;
            }

            sum +=
                static_cast<float>(
                    pixel(px, py)
                );

            ++samples;
        }
    }

    if (samples == 0) {
        return;
    }

    float background =
        sum /
        static_cast<float>(samples);

    float residual =
        center - background;

    if (residual < threshold) {
        return;
    }

    int slot =
        atomicAdd(count, 1);

    if (slot < rawCapacity) {
        output[slot].x = x;
        output[slot].y = y;
        output[slot].strength =
            residual;
    }
}

class NvJpegDecoder {
public:
    NvJpegDecoder() {
        NVJPEG_CHECK(
            nvjpegCreateSimple(
                &handle_
            )
        );

        NVJPEG_CHECK(
            nvjpegJpegStateCreate(
                handle_,
                &state_
            )
        );

        CUDA_CHECK(
            cudaStreamCreate(
                &stream_
            )
        );
    }

    ~NvJpegDecoder() {
        if (stream_) {
            cudaStreamDestroy(stream_);
        }

        if (state_) {
            nvjpegJpegStateDestroy(
                state_
            );
        }

        if (handle_) {
            nvjpegDestroy(handle_);
        }
    }

    unsigned char* decodeToDeviceGray(
        const std::vector<unsigned char>& jpeg,
        int& width,
        int& height,
        int& pitch
    ) {
        int components = 0;
        nvjpegChromaSubsampling_t
            subsampling;

        int widths[NVJPEG_MAX_COMPONENT]{};
        int heights[NVJPEG_MAX_COMPONENT]{};

        NVJPEG_CHECK(
            nvjpegGetImageInfo(
                handle_,
                jpeg.data(),
                jpeg.size(),
                &components,
                &subsampling,
                widths,
                heights
            )
        );

        width = widths[0];
        height = heights[0];
        pitch = width;

        if (
            width <= 0
            ||
            height <= 0
        ) {
            throw std::runtime_error(
                "nvJPEG reported invalid dimensions"
            );
        }

        unsigned char* dGray =
            nullptr;

        CUDA_CHECK(
            cudaMalloc(
                &dGray,
                static_cast<size_t>(
                    pitch
                ) * height
            )
        );

        nvjpegImage_t output{};
        output.channel[0] = dGray;
        output.pitch[0] =
            static_cast<unsigned int>(
                pitch
            );

        nvjpegStatus_t status =
            nvjpegDecode(
                handle_,
                state_,
                jpeg.data(),
                jpeg.size(),
                NVJPEG_OUTPUT_Y,
                &output,
                stream_
            );

        if (
            status
            != NVJPEG_STATUS_SUCCESS
        ) {
            cudaFree(dGray);

            throw std::runtime_error(
                "nvjpegDecode failed with code "
                + std::to_string(
                    static_cast<int>(status)
                )
            );
        }

        CUDA_CHECK(
            cudaStreamSynchronize(
                stream_
            )
        );

        return dGray;
    }

private:
    nvjpegHandle_t handle_ =
        nullptr;

    nvjpegJpegState_t state_ =
        nullptr;

    cudaStream_t stream_ =
        nullptr;
};

static std::vector<Detection>
detectFrameCuda(
    NvJpegDecoder& decoder,
    ImageMeta& meta,
    int frameIndex,
    const Args& args,
    const PassParams& pass
) {
    std::vector<unsigned char> jpeg =
        readFile(meta.path);

    int width = 0;
    int height = 0;
    int pitch = 0;

    unsigned char* dGray =
        decoder.decodeToDeviceGray(
            jpeg,
            width,
            height,
            pitch
        );

    meta.width = width;
    meta.height = height;

    // GPU atomicAdd has nondeterministic scheduling. Truncating the
    // FIRST maxPoints*6 hits makes two byte-identical images return
    // different coordinate subsets, creating fictitious motion.
    // Allocate room for every possible pixel-local-maximum instead.
    // LASCO input is 512/1024 px, so at 1024 this is ~12 MiB.
    const long long pixelCapacity =
        static_cast<long long>(width) * static_cast<long long>(height);
    if (pixelCapacity <= 0 ||
        pixelCapacity > static_cast<long long>(std::numeric_limits<int>::max())) {
        cudaFree(dGray);
        throw std::runtime_error("Invalid CUDA detection image pixel capacity");
    }
    const int rawCapacity = static_cast<int>(pixelCapacity);

    DeviceDetection* dOutput =
        nullptr;

    int* dCount =
        nullptr;

    CUDA_CHECK(
        cudaMalloc(
            &dOutput,
            sizeof(DeviceDetection)
                * rawCapacity
        )
    );

    CUDA_CHECK(
        cudaMalloc(
            &dCount,
            sizeof(int)
        )
    );

    CUDA_CHECK(
        cudaMemset(
            dCount,
            0,
            sizeof(int)
        )
    );

    dim3 block(16, 16);

    dim3 grid(
        (width + block.x - 1)
            / block.x,
        (height + block.y - 1)
            / block.y
    );

    float centerX =
        (width - 1) * 0.5f;

    float centerY =
        (height - 1) * 0.5f;

    float centerRadius =
        std::min(width, height)
        * args.centerMaskFraction;

    detectKernel<<<grid, block>>>(
        dGray,
        width,
        height,
        pitch,
        args.border,
        args.edgeMask,
        centerX,
        centerY,
        centerRadius * centerRadius,
        args.bgRadius,
        pass.threshold,
        rawCapacity,
        dOutput,
        dCount
    );

    CUDA_CHECK(
        cudaGetLastError()
    );

    CUDA_CHECK(
        cudaDeviceSynchronize()
    );

    int count = 0;

    CUDA_CHECK(
        cudaMemcpy(
            &count,
            dCount,
            sizeof(int),
            cudaMemcpyDeviceToHost
        )
    );

    if (count > rawCapacity) {
        cudaFree(dGray);
        cudaFree(dOutput);
        cudaFree(dCount);
        throw std::runtime_error("CUDA detection capacity exceeded; refusing truncated point set");
    }

    std::vector<DeviceDetection>
        raw(count);

    if (count > 0) {
        CUDA_CHECK(
            cudaMemcpy(
                raw.data(),
                dOutput,
                sizeof(DeviceDetection)
                    * count,
                cudaMemcpyDeviceToHost
            )
        );
    }

    cudaFree(dGray);
    cudaFree(dOutput);
    cudaFree(dCount);

    std::sort(
        raw.begin(),
        raw.end(),
        [](const DeviceDetection& a,
           const DeviceDetection& b) {
            if (a.strength != b.strength) {
                return a.strength > b.strength;
            }
            // Deterministic tie-break independent of GPU atomic order.
            if (a.y != b.y) {
                return a.y < b.y;
            }
            return a.x < b.x;
        }
    );

    if (args.spatialBalancedPeaks) {
        raw = comet::selectSpatiallyBalanced(std::move(raw), width, height, pass.maxPoints);
    } else if (static_cast<int>(raw.size()) > pass.maxPoints) {
        raw.resize(pass.maxPoints);
    }

    std::vector<Detection>
        detections;

    detections.reserve(
        raw.size()
    );

    for (const auto& item : raw) {
        detections.push_back({
            frameIndex,
            static_cast<float>(
                item.x
            ),
            static_cast<float>(
                item.y
            ),
            item.strength
        });
    }

    return detections;
}

static long long artifactKey(
    int bx,
    int by
) {
    return
        (
            static_cast<long long>(
                bx
            ) << 32
        )
        ^
        static_cast<unsigned int>(
            by
        );
}

static size_t removeRecurringArtifacts(
    std::vector<std::vector<Detection>>& detections,
    const std::vector<ImageMeta>& images,
    int binSize,
    int minDays
) {
    std::unordered_map<
        long long,
        std::unordered_set<int>
    > daysByBin;

    for (
        size_t frame = 0;
        frame < detections.size();
        ++frame
    ) {
        int date =
            utcDateKey(
                images[frame].timestamp
            );

        for (
            const auto& d :
            detections[frame]
        ) {
            int bx =
                static_cast<int>(
                    std::lround(d.x)
                ) / binSize;

            int by =
                static_cast<int>(
                    std::lround(d.y)
                ) / binSize;

            daysByBin[
                artifactKey(
                    bx,
                    by
                )
            ].insert(date);
        }
    }

    size_t removed = 0;

    for (
        auto& frameDetections :
        detections
    ) {
        frameDetections.erase(
            std::remove_if(
                frameDetections.begin(),
                frameDetections.end(),
                [&](const Detection& d) {
                    int bx =
                        static_cast<int>(
                            std::lround(d.x)
                        ) / binSize;

                    int by =
                        static_cast<int>(
                            std::lround(d.y)
                        ) / binSize;

                    int maxDays = 0;

                    for (
                        int oy = -1;
                        oy <= 1;
                        ++oy
                    ) {
                        for (
                            int ox = -1;
                            ox <= 1;
                            ++ox
                        ) {
                            auto it =
                                daysByBin.find(
                                    artifactKey(
                                        bx + ox,
                                        by + oy
                                    )
                                );

                            if (
                                it
                                != daysByBin.end()
                            ) {
                                maxDays =
                                    std::max(
                                        maxDays,
                                        static_cast<int>(
                                            it->second.size()
                                        )
                                    );
                            }
                        }
                    }

                    if (
                        maxDays
                        >= minDays
                    ) {
                        ++removed;
                        return true;
                    }

                    return false;
                }
            ),
            frameDetections.end()
        );
    }

    return removed;
}

__global__ void populateOccupancyKernel(
    const int16_t* xs,
    const int16_t* ys,
    const int* counts,
    int maxPoints,
    int frameCount,
    int width,
    int height,
    unsigned char* occupancy
) {
    long long index =
        static_cast<long long>(
            blockIdx.x
        ) * blockDim.x
        + threadIdx.x;

    long long total =
        static_cast<long long>(
            frameCount
        ) * maxPoints;

    if (index >= total) {
        return;
    }

    int frame =
        static_cast<int>(
            index / maxPoints
        );

    int point =
        static_cast<int>(
            index % maxPoints
        );

    if (
        point
        >= counts[frame]
    ) {
        return;
    }

    int x =
        xs[index];

    int y =
        ys[index];

    if (
        x < 0
        ||
        y < 0
        ||
        x >= width
        ||
        y >= height
    ) {
        return;
    }

    long long pixel =
        (
            static_cast<long long>(
                frame
            ) * height
            + y
        ) * width
        + x;

    occupancy[pixel] = 1;
}

__global__ void searchSeedsKernel(
    const int16_t* xs,
    const int16_t* ys,
    const int* counts,
    const float* timesHours,
    const unsigned char* occupancy,
    int frameCount,
    int maxPoints,
    int width,
    int height,
    int startFrame,
    int seedStep,
    int deltaRadius,
    int searchWindow,
    int tolerance,
    int minFrames,
    float maxComponentSpeed,
    float maxSeedGapHours,
    GpuSeed* output,
    int outputCapacity,
    int* outputCount
) {
    int countStart =
        counts[startFrame];

    int deltaWidth =
        2 * deltaRadius + 1;

    long long combinations =
        static_cast<long long>(
            countStart
        )
        * deltaWidth
        * deltaWidth;

    long long threadIndex =
        static_cast<long long>(
            blockIdx.x
        ) * blockDim.x
        + threadIdx.x;

    if (
        threadIndex
        >= combinations
    ) {
        return;
    }

    int pointIndex =
        static_cast<int>(
            threadIndex
            /
            (
                deltaWidth
                * deltaWidth
            )
        );

    int deltaIndex =
        static_cast<int>(
            threadIndex
            %
            (
                deltaWidth
                * deltaWidth
            )
        );

    int dx =
        deltaIndex
        % deltaWidth
        - deltaRadius;

    int dy =
        deltaIndex
        / deltaWidth
        - deltaRadius;

    if (
        dx == 0
        &&
        dy == 0
    ) {
        return;
    }

    int secondFrame =
        startFrame
        + seedStep;

    if (
        secondFrame
        >= frameCount
    ) {
        return;
    }

    float dt =
        timesHours[secondFrame]
        - timesHours[startFrame];

    if (
        dt <= 0.0f
        ||
        dt > maxSeedGapHours
    ) {
        return;
    }

    long long base =
        static_cast<long long>(
            startFrame
        ) * maxPoints;

    int x0 =
        xs[base + pointIndex];

    int y0 =
        ys[base + pointIndex];

    int x1 =
        x0 + dx;

    int y1 =
        y0 + dy;

    if (
        x1 < 0
        ||
        y1 < 0
        ||
        x1 >= width
        ||
        y1 >= height
    ) {
        return;
    }

    long long secondPixel =
        (
            static_cast<long long>(
                secondFrame
            ) * height
            + y1
        ) * width
        + x1;

    if (
        occupancy[secondPixel]
        == 0
    ) {
        return;
    }

    float vx =
        dx / dt;

    float vy =
        dy / dt;

    if (
        fabsf(vx)
        > maxComponentSpeed
        ||
        fabsf(vy)
        > maxComponentSpeed
    ) {
        return;
    }

    float speed =
        sqrtf(
            vx * vx
            + vy * vy
        );

    if (speed < 0.25f) {
        return;
    }

    if (
        fabsf(vx) > 1.0f
        &&
        fabsf(vy)
        /
        fmaxf(
            fabsf(vx),
            1e-6f
        )
        < 0.08f
    ) {
        return;
    }

    int hits = 2;
    float error2 = 0.0f;

    int lastFrame =
        min(
            frameCount,
            startFrame
            + searchWindow
        );

    for (
        int frame =
            secondFrame + 1;
        frame < lastFrame;
        ++frame
    ) {
        float elapsed =
            timesHours[frame]
            - timesHours[startFrame];

        if (elapsed <= 0.0f) {
            continue;
        }

        float predictedX =
            x0 + vx * elapsed;

        float predictedY =
            y0 + vy * elapsed;

        int centerX =
            static_cast<int>(
                nearbyintf(
                    predictedX
                )
            );

        int centerY =
            static_cast<int>(
                nearbyintf(
                    predictedY
                )
            );

        float bestDistance2 =
            1e30f;

        bool found = false;

        for (
            int oy = -tolerance;
            oy <= tolerance;
            ++oy
        ) {
            for (
                int ox = -tolerance;
                ox <= tolerance;
                ++ox
            ) {
                int x =
                    centerX + ox;

                int y =
                    centerY + oy;

                if (
                    x < 0
                    ||
                    y < 0
                    ||
                    x >= width
                    ||
                    y >= height
                ) {
                    continue;
                }

                long long pixel =
                    (
                        static_cast<long long>(
                            frame
                        ) * height
                        + y
                    ) * width
                    + x;

                if (
                    occupancy[pixel]
                    == 0
                ) {
                    continue;
                }

                float ex =
                    static_cast<float>(x)
                    - predictedX;

                float ey =
                    static_cast<float>(y)
                    - predictedY;

                float distance2 =
                    ex * ex
                    + ey * ey;

                if (
                    distance2
                    < bestDistance2
                ) {
                    bestDistance2 =
                        distance2;

                    found = true;
                }
            }
        }

        if (found) {
            ++hits;
            error2 +=
                bestDistance2;
        }
    }

    if (
        hits
        < minFrames
    ) {
        return;
    }

    float predictionRms =
        sqrtf(
            error2
            /
            fmaxf(
                static_cast<float>(
                    hits - 2
                ),
                1.0f
            )
        );

    if (
        predictionRms
        > 2.5f
    ) {
        return;
    }

    int slot =
        atomicAdd(
            outputCount,
            1
        );

    if (
        slot
        < outputCapacity
    ) {
        GpuSeed seed;
        seed.startFrame =
            startFrame;
        seed.seedStep =
            seedStep;
        seed.x0 =
            x0;
        seed.y0 =
            y0;
        seed.vx =
            vx;
        seed.vy =
            vy;
        seed.hits =
            hits;
        seed.predictionRms =
            predictionRms;

        output[slot] =
            seed;
    }
}

class GpuTrajectorySearch {
public:
    GpuTrajectorySearch(
        const std::vector<std::vector<Detection>>& detections,
        const std::vector<ImageMeta>& images,
        int width,
        int height,
        int maxPoints
    )
        :
        frameCount_(
            static_cast<int>(
                detections.size()
            )
        ),
        width_(width),
        height_(height),
        maxPoints_(maxPoints)
    {
        if (
            frameCount_ <= 0
            ||
            width_ <= 0
            ||
            height_ <= 0
            ||
            maxPoints_ <= 0
        ) {
            throw std::runtime_error(
                "Invalid GPU trajectory dimensions"
            );
        }

        size_t pointSlots =
            static_cast<size_t>(
                frameCount_
            ) * maxPoints_;

        std::vector<int16_t>
            hostX(
                pointSlots,
                -1
            );

        std::vector<int16_t>
            hostY(
                pointSlots,
                -1
            );

        std::vector<int>
            hostCounts(
                frameCount_,
                0
            );

        std::vector<float>
            hostTimes(
                frameCount_,
                0.0f
            );

        std::time_t t0 =
            images.front().timestamp;

        for (
            int frame = 0;
            frame < frameCount_;
            ++frame
        ) {
            int count =
                std::min(
                    static_cast<int>(
                        detections[frame].size()
                    ),
                    maxPoints_
                );

            hostCounts[frame] =
                count;

            for (
                int i = 0;
                i < count;
                ++i
            ) {
                size_t index =
                    static_cast<size_t>(
                        frame
                    ) * maxPoints_
                    + i;

                hostX[index] =
                    static_cast<int16_t>(
                        std::lround(
                            detections[frame][i].x
                        )
                    );

                hostY[index] =
                    static_cast<int16_t>(
                        std::lround(
                            detections[frame][i].y
                        )
                    );
            }

            hostTimes[frame] =
                static_cast<float>(
                    std::difftime(
                        images[frame].timestamp,
                        t0
                    )
                    / 3600.0
                );
        }

        CUDA_CHECK(
            cudaMalloc(
                &dX_,
                sizeof(int16_t)
                    * pointSlots
            )
        );

        CUDA_CHECK(
            cudaMalloc(
                &dY_,
                sizeof(int16_t)
                    * pointSlots
            )
        );

        CUDA_CHECK(
            cudaMalloc(
                &dCounts_,
                sizeof(int)
                    * frameCount_
            )
        );

        CUDA_CHECK(
            cudaMalloc(
                &dTimes_,
                sizeof(float)
                    * frameCount_
            )
        );

        CUDA_CHECK(
            cudaMemcpy(
                dX_,
                hostX.data(),
                sizeof(int16_t)
                    * pointSlots,
                cudaMemcpyHostToDevice
            )
        );

        CUDA_CHECK(
            cudaMemcpy(
                dY_,
                hostY.data(),
                sizeof(int16_t)
                    * pointSlots,
                cudaMemcpyHostToDevice
            )
        );

        CUDA_CHECK(
            cudaMemcpy(
                dCounts_,
                hostCounts.data(),
                sizeof(int)
                    * frameCount_,
                cudaMemcpyHostToDevice
            )
        );

        CUDA_CHECK(
            cudaMemcpy(
                dTimes_,
                hostTimes.data(),
                sizeof(float)
                    * frameCount_,
                cudaMemcpyHostToDevice
            )
        );

        occupancyBytes_ =
            static_cast<size_t>(
                frameCount_
            )
            * width_
            * height_;

        CUDA_CHECK(
            cudaMalloc(
                &dOccupancy_,
                occupancyBytes_
            )
        );

        CUDA_CHECK(
            cudaMemset(
                dOccupancy_,
                0,
                occupancyBytes_
            )
        );

        long long totalSlots =
            static_cast<long long>(
                frameCount_
            ) * maxPoints_;

        int threads = 256;

        int blocks =
            static_cast<int>(
                (
                    totalSlots
                    + threads
                    - 1
                )
                / threads
            );

        populateOccupancyKernel
            <<<blocks, threads>>>(
                dX_,
                dY_,
                dCounts_,
                maxPoints_,
                frameCount_,
                width_,
                height_,
                dOccupancy_
            );

        CUDA_CHECK(
            cudaGetLastError()
        );

        CUDA_CHECK(
            cudaDeviceSynchronize()
        );
    }

    ~GpuTrajectorySearch() {
        if (dOccupancy_) {
            cudaFree(
                dOccupancy_
            );
        }

        if (dTimes_) {
            cudaFree(
                dTimes_
            );
        }

        if (dCounts_) {
            cudaFree(
                dCounts_
            );
        }

        if (dY_) {
            cudaFree(
                dY_
            );
        }

        if (dX_) {
            cudaFree(
                dX_
            );
        }
    }

    std::vector<GpuSeed> search(
        const std::vector<std::vector<Detection>>& detections,
        const std::vector<ImageMeta>& images,
        const Args& args,
        const PassParams& pass
    ) {
        std::vector<GpuSeed>
            collected;

        float maxSeedGapHours =
            args.maxSeedGapMinutes
            / 60.0f;

        for (
            int startFrame = 0;
            startFrame
                + args.minFrames
                <= frameCount_;
            ++startFrame
        ) {
            int startCount =
                static_cast<int>(
                    detections[startFrame].size()
                );

            if (startCount <= 0) {
                continue;
            }

            std::vector<GpuSeed>
                perStart;

            for (
                int seedStep = 1;
                seedStep
                    <= pass.seedStepMax;
                ++seedStep
            ) {
                int secondFrame =
                    startFrame
                    + seedStep;

                if (
                    secondFrame
                    >= frameCount_
                ) {
                    break;
                }

                double dtHours =
                    std::difftime(
                        images[secondFrame].timestamp,
                        images[startFrame].timestamp
                    )
                    / 3600.0;

                if (
                    dtHours <= 0.0
                    ||
                    dtHours
                        > maxSeedGapHours
                ) {
                    continue;
                }

                int deltaRadius =
                    static_cast<int>(
                        std::ceil(
                            pass.maxComponentSpeed
                            * dtHours
                        )
                    )
                    + 1;

                deltaRadius =
                    std::max(
                        deltaRadius,
                        1
                    );

                int deltaWidth =
                    2 * deltaRadius
                    + 1;

                long long combinations =
                    static_cast<long long>(
                        startCount
                    )
                    * deltaWidth
                    * deltaWidth;

                GpuSeed* dOutput =
                    nullptr;

                int* dOutputCount =
                    nullptr;

                CUDA_CHECK(
                    cudaMalloc(
                        &dOutput,
                        sizeof(GpuSeed)
                            * args.gpuSeedCapacity
                    )
                );

                CUDA_CHECK(
                    cudaMalloc(
                        &dOutputCount,
                        sizeof(int)
                    )
                );

                CUDA_CHECK(
                    cudaMemset(
                        dOutputCount,
                        0,
                        sizeof(int)
                    )
                );

                int threads =
                    256;

                int blocks =
                    static_cast<int>(
                        (
                            combinations
                            + threads
                            - 1
                        )
                        / threads
                    );

                searchSeedsKernel
                    <<<blocks, threads>>>(
                        dX_,
                        dY_,
                        dCounts_,
                        dTimes_,
                        dOccupancy_,
                        frameCount_,
                        maxPoints_,
                        width_,
                        height_,
                        startFrame,
                        seedStep,
                        deltaRadius,
                        pass.searchWindow,
                        pass.tolerance,
                        args.minFrames,
                        pass.maxComponentSpeed,
                        maxSeedGapHours,
                        dOutput,
                        args.gpuSeedCapacity,
                        dOutputCount
                    );

                CUDA_CHECK(
                    cudaGetLastError()
                );

                CUDA_CHECK(
                    cudaDeviceSynchronize()
                );

                int outputCount = 0;

                CUDA_CHECK(
                    cudaMemcpy(
                        &outputCount,
                        dOutputCount,
                        sizeof(int),
                        cudaMemcpyDeviceToHost
                    )
                );

                int copied =
                    std::min(
                        outputCount,
                        args.gpuSeedCapacity
                    );

                if (copied > 0) {
                    std::vector<GpuSeed>
                        temp(copied);

                    CUDA_CHECK(
                        cudaMemcpy(
                            temp.data(),
                            dOutput,
                            sizeof(GpuSeed)
                                * copied,
                            cudaMemcpyDeviceToHost
                        )
                    );

                    perStart.insert(
                        perStart.end(),
                        temp.begin(),
                        temp.end()
                    );
                }

                cudaFree(
                    dOutput
                );

                cudaFree(
                    dOutputCount
                );
            }

            std::sort(
                perStart.begin(),
                perStart.end(),
                [](const GpuSeed& a,
                   const GpuSeed& b) {
                    if (
                        a.hits
                        != b.hits
                    ) {
                        return
                            a.hits
                            > b.hits;
                    }

                    return
                        a.predictionRms
                        < b.predictionRms;
                }
            );

            if (
                static_cast<int>(
                    perStart.size()
                )
                > args.seedsPerStartKeep
            ) {
                perStart.resize(
                    args.seedsPerStartKeep
                );
            }

            collected.insert(
                collected.end(),
                perStart.begin(),
                perStart.end()
            );

            if (
                (startFrame + 1)
                % 100
                == 0
            ) {
                std::cout
                    << "    GPU trajectory starts: "
                    << (startFrame + 1)
                    << "/"
                    << frameCount_
                    << " | seeds kept="
                    << collected.size()
                    << "\n";
            }
        }

        std::sort(
            collected.begin(),
            collected.end(),
            [](const GpuSeed& a,
               const GpuSeed& b) {
                if (
                    a.hits
                    != b.hits
                ) {
                    return
                        a.hits
                        > b.hits;
                }

                return
                    a.predictionRms
                    < b.predictionRms;
            }
        );

        if (
            static_cast<int>(
                collected.size()
            )
            > args.rawSeedKeep
        ) {
            collected.resize(
                args.rawSeedKeep
            );
        }

        return collected;
    }

private:
    int frameCount_ = 0;
    int width_ = 0;
    int height_ = 0;
    int maxPoints_ = 0;

    int16_t* dX_ =
        nullptr;

    int16_t* dY_ =
        nullptr;

    int* dCounts_ =
        nullptr;

    float* dTimes_ =
        nullptr;

    unsigned char* dOccupancy_ =
        nullptr;

    size_t occupancyBytes_ = 0;
};

static std::vector<Detection>
reconstructSeed(
    const GpuSeed& seed,
    const std::vector<std::vector<Detection>>& detections,
    const std::vector<ImageMeta>& images,
    const PassParams& pass
) {
    std::vector<Detection>
        points;

    int endFrame =
        std::min(
            static_cast<int>(
                detections.size()
            ),
            seed.startFrame
            + pass.searchWindow
        );

    std::time_t t0 =
        images[seed.startFrame].timestamp;

    for (
        int frame = seed.startFrame;
        frame < endFrame;
        ++frame
    ) {
        double elapsed =
            std::difftime(
                images[frame].timestamp,
                t0
            )
            / 3600.0;

        float predictedX =
            seed.x0
            + seed.vx
                * static_cast<float>(
                    elapsed
                );

        float predictedY =
            seed.y0
            + seed.vy
                * static_cast<float>(
                    elapsed
                );

        const Detection* best =
            nullptr;

        float bestDistance =
            std::numeric_limits<float>::max();

        for (
            const auto& candidate :
            detections[frame]
        ) {
            float distance =
                std::hypot(
                    candidate.x
                        - predictedX,
                    candidate.y
                        - predictedY
                );

            if (
                distance
                    < bestDistance
            ) {
                bestDistance =
                    distance;

                best =
                    &candidate;
            }
        }

        if (
            best
            &&
            bestDistance
                <= pass.tolerance
        ) {
            points.push_back(
                *best
            );
        }
    }

    return points;
}

static Track fitTrack(
    std::vector<Detection> points,
    const std::vector<ImageMeta>& images,
    const std::string& passName
) {
    std::sort(
        points.begin(),
        points.end(),
        [](const Detection& a,
           const Detection& b) {
            return
                a.frame
                < b.frame;
        }
    );

    Track track;
    track.passName =
        passName;

    if (points.size() < 2) {
        return track;
    }

    std::time_t t0 =
        images[
            points.front().frame
        ].timestamp;

    double n =
        static_cast<double>(
            points.size()
        );

    double sumT = 0.0;
    double sumTT = 0.0;
    double sumX = 0.0;
    double sumTX = 0.0;
    double sumY = 0.0;
    double sumTY = 0.0;

    double meanStrength = 0.0;

    for (
        const auto& p :
        points
    ) {
        double t =
            std::difftime(
                images[p.frame].timestamp,
                t0
            )
            / 3600.0;

        sumT += t;
        sumTT += t * t;
        sumX += p.x;
        sumTX += t * p.x;
        sumY += p.y;
        sumTY += t * p.y;

        meanStrength +=
            p.strength;
    }

    meanStrength /= n;

    double denominator =
        n * sumTT
        - sumT * sumT;

    if (
        std::abs(denominator)
        < 1e-12
    ) {
        return track;
    }

    double vx =
        (
            n * sumTX
            - sumT * sumX
        )
        / denominator;

    double vy =
        (
            n * sumTY
            - sumT * sumY
        )
        / denominator;

    double bx =
        (
            sumX
            - vx * sumT
        )
        / n;

    double by =
        (
            sumY
            - vy * sumT
        )
        / n;

    double error2 = 0.0;
    double strengthVariance = 0.0;

    for (
        const auto& p :
        points
    ) {
        double t =
            std::difftime(
                images[p.frame].timestamp,
                t0
            )
            / 3600.0;

        double ex =
            p.x
            - (
                vx * t
                + bx
            );

        double ey =
            p.y
            - (
                vy * t
                + by
            );

        error2 +=
            ex * ex
            + ey * ey;

        double ds =
            p.strength
            - meanStrength;

        strengthVariance +=
            ds * ds;
    }

    double strengthStd =
        std::sqrt(
            strengthVariance
            / n
        );

    track.points =
        std::move(points);

    track.vx =
        static_cast<float>(
            vx
        );

    track.vy =
        static_cast<float>(
            vy
        );

    track.speed =
        static_cast<float>(
            std::hypot(
                vx,
                vy
            )
        );

    track.rms =
        static_cast<float>(
            std::sqrt(
                error2
                / n
            )
        );

    track.brightnessCv =
        static_cast<float>(
            strengthStd
            /
            std::max(
                meanStrength,
                1e-6
            )
        );

    const auto& first =
        track.points.front();

    const auto& last =
        track.points.back();

    int width =
        images[
            first.frame
        ].width;

    int height =
        images[
            first.frame
        ].height;

    double cx =
        (width - 1) * 0.5;

    double cy =
        (height - 1) * 0.5;

    double startRadius =
        std::hypot(
            first.x - cx,
            first.y - cy
        );

    double endRadius =
        std::hypot(
            last.x - cx,
            last.y - cy
        );

    double durationHours =
        std::difftime(
            images[
                last.frame
            ].timestamp,
            images[
                first.frame
            ].timestamp
        )
        / 3600.0;

    if (durationHours > 0.0) {
        track.sunwardRate =
            static_cast<float>(
                (
                    startRadius
                    - endRadius
                )
                / durationHours
            );
    }

    double score =
        track.points.size()
        * 12.0;

    score -=
        track.rms
        * 12.0;

    if (
        std::abs(track.vx)
        > 0.35f
        &&
        std::abs(track.vy)
        > 0.35f
    ) {
        score +=
            8.0;
    }

    if (
        track.sunwardRate
        > 0.0f
    ) {
        score +=
            std::min(
                static_cast<double>(
                    track.sunwardRate
                ) * 3.0,
                25.0
            );
    }
    else {
        score +=
            std::max(
                static_cast<double>(
                    track.sunwardRate
                ),
                -8.0
            );
    }

    if (
        track.brightnessCv
        <= 0.35f
    ) {
        score += 6.0;
    }
    else if (
        track.brightnessCv
        > 1.0f
    ) {
        score -= 8.0;
    }

    track.score =
        static_cast<float>(
            score
        );

    bool high =
        track.points.size() >= 7
        &&
        track.rms <= 0.9f
        &&
        std::abs(track.vx) > 0.35f
        &&
        std::abs(track.vy) > 0.35f
        &&
        track.brightnessCv <= 1.0f
        &&
        track.sunwardRate > 0.0f;

    bool medium =
        track.points.size() >= 5
        &&
        track.rms <= 1.5f
        &&
        !(
            std::abs(track.vx) > 1.0f
            &&
            std::abs(track.vy)
                / std::max(
                    std::abs(track.vx),
                    1e-6f
                )
                < 0.10f
        );

    track.priority =
        high
        ? "HIGH"
        : (
            medium
            ? "MEDIUM"
            : "LOW"
        );

    return track;
}

static bool tracksDuplicate(
    const Track& a,
    const Track& b
) {
    int shared = 0;
    double totalDistance = 0.0;

    for (
        const auto& pa :
        a.points
    ) {
        for (
            const auto& pb :
            b.points
        ) {
            if (
                pa.frame
                == pb.frame
            ) {
                ++shared;

                totalDistance +=
                    std::hypot(
                        pa.x - pb.x,
                        pa.y - pb.y
                    );

                break;
            }
        }
    }

    return
        shared >= 3
        &&
        totalDistance
            / shared
            < 4.0;
}

static void mergeTracks(
    std::vector<Track>& global,
    std::vector<Track> incoming,
    int maxCandidates
) {
    std::sort(
        incoming.begin(),
        incoming.end(),
        [](const Track& a,
           const Track& b) {
            return
                a.score
                > b.score;
        }
    );

    for (
        auto& candidate :
        incoming
    ) {
        bool duplicate =
            false;

        for (
            auto& existing :
            global
        ) {
            if (
                tracksDuplicate(
                    candidate,
                    existing
                )
            ) {
                duplicate =
                    true;

                if (
                    candidate.score
                    > existing.score
                ) {
                    existing =
                        std::move(
                            candidate
                        );
                }

                break;
            }
        }

        if (!duplicate) {
            global.push_back(
                std::move(
                    candidate
                )
            );
        }
    }

    std::sort(
        global.begin(),
        global.end(),
        [](const Track& a,
           const Track& b) {
            return
                a.score
                > b.score;
        }
    );

    if (
        static_cast<int>(
            global.size()
        )
        > maxCandidates
    ) {
        global.resize(
            maxCandidates
        );
    }
}

static PassParams makePass(
    int level,
    const Args& args
) {
    PassParams pass;

    std::ostringstream name;
    name
        << "deep-"
        << std::setw(2)
        << std::setfill('0')
        << (level + 1);

    pass.name =
        name.str();

    pass.threshold =
        std::max(
            5.0f,
            18.0f
            - level * 0.75f
        );

    pass.maxPoints =
        std::min(
            6000,
            700
            + level * 300
        );

    pass.searchWindow =
        std::min(
            72,
            16
            + level * 3
        );

    pass.tolerance =
        (
            level % 3 == 2
        )
        ? 3
        : 2;

    pass.seedStepMax =
        std::min(
            3,
            1
            + level / 5
        );

    pass.artifactMinDays =
        std::max(
            3,
            6
            - level / 6
        );

    if (
        args.camera == "c3"
    ) {
        pass.maxComponentSpeed =
            level < 8
            ? 12.0f
            : 15.0f;
    }
    else {
        pass.maxComponentSpeed =
            level < 8
            ? 80.0f
            : 95.0f;
    }

    return pass;
}

static std::vector<Track> runPass(
    std::vector<ImageMeta>& images,
    const Args& args,
    const PassParams& pass
) {
    std::cout
        << "\n============================================================\n"
        << "PASS "
        << pass.name
        << "\n"
        << "threshold="
        << pass.threshold
        << " maxPoints="
        << pass.maxPoints
        << " window="
        << pass.searchWindow
        << " tolerance="
        << pass.tolerance
        << " seedStepMax="
        << pass.seedStepMax
        << " artifactMinDays="
        << pass.artifactMinDays
        << "\n"
        << "============================================================\n";

    auto passStart =
        std::chrono::steady_clock::now();

    NvJpegDecoder decoder;

    std::vector<
        std::vector<Detection>
    > detections;

    detections.reserve(
        images.size()
    );

    size_t rawDetectionCount =
        0;

    for (
        size_t frame = 0;
        frame < images.size();
        ++frame
    ) {
        auto frameDetections =
            detectFrameCuda(
                decoder,
                images[frame],
                static_cast<int>(
                    frame
                ),
                args,
                pass
            );

        rawDetectionCount +=
            frameDetections.size();

        detections.push_back(
            std::move(
                frameDetections
            )
        );

        if (
            (frame + 1)
            % 100
            == 0
            ||
            frame + 1
                == images.size()
        ) {
            std::cout
                << "    detected frames: "
                << (frame + 1)
                << "/"
                << images.size()
                << " | raw points="
                << rawDetectionCount
                << "\n";
        }
    }

    if (
        images.front().width
        <= 0
        ||
        images.front().height
        <= 0
    ) {
        throw std::runtime_error(
            "Image dimensions were not established"
        );
    }

    size_t removed =
        removeRecurringArtifacts(
            detections,
            images,
            args.artifactBin,
            pass.artifactMinDays
        );

    size_t stationaryRemoved = 0;
    if (args.withinWindowStationaryFilter) {
        stationaryRemoved = comet::removeWindowStationaryPoints(
            detections,
            args.stationaryBin,
            args.stationaryMinFrameFraction
        );
    }

    size_t filteredCount =
        0;

    for (
        const auto& frame :
        detections
    ) {
        filteredCount +=
            frame.size();
    }

    std::cout
        << "    recurring-coordinate detections removed: "
        << removed
        << "\n"
        << "    within-window stationary detections removed: "
        << stationaryRemoved
        << (args.withinWindowStationaryFilter ? " (enabled)" : " (disabled)")
        << "\n"
        << "    points remaining: "
        << filteredCount
        << "\n";

    if (filteredCount == 0) {
        return {};
    }

    const auto occupied = args.trajectoryNullControl
        ? comet::makeFrameOccupancy(detections)
        : comet::FrameOccupancy{};

    GpuTrajectorySearch gpuSearch(
        detections,
        images,
        images.front().width,
        images.front().height,
        pass.maxPoints
    );

    std::cout
        << "    GPU trajectory search...\n";

    auto seeds =
        gpuSearch.search(
            detections,
            images,
            args,
            pass
        );

    std::cout
        << "    top GPU seeds retained: "
        << seeds.size()
        << "\n";

    std::vector<Track>
        tracks;

    tracks.reserve(
        seeds.size()
    );
    size_t invalidTrajectories = 0;
    size_t nullControlRejected = 0;
    size_t nullControlTested = 0;

    for (
        const auto& seed :
        seeds
    ) {
        auto points =
            reconstructSeed(
                seed,
                detections,
                images,
                pass
            );

        if (
            static_cast<int>(
                points.size()
            )
            < args.minFrames
        ) {
            continue;
        }

        Track track =
            fitTrack(
                std::move(
                    points
                ),
                images,
                pass.name
            );

        float componentLimit =
            pass.maxComponentSpeed;

        if (
            track.points.size()
                < static_cast<size_t>(
                    args.minFrames
                )
            ||
            track.rms > 2.0f
            ||
            std::abs(track.vx)
                > componentLimit
            ||
            std::abs(track.vy)
                > componentLimit
        ) {
            continue;
        }

        if (args.trajectoryIntegrityFilter &&
            !comet::consistentEndpointVelocity(track, images)) {
            ++invalidTrajectories;
            continue;
        }
        if (args.trajectoryNullControl) {
            const auto evidence = comet::measurePathNullEvidence(
                track, images, occupied, images.front().width,
                images.front().height, pass.tolerance);
            if (evidence.evaluable) {
                ++nullControlTested;
                if (!evidence.supported) {
                    ++nullControlRejected;
                    continue;
                }
            }
        }

        if (
            std::abs(track.vx)
            > 1.0f
            &&
            std::abs(track.vy)
                / std::max(
                    std::abs(track.vx),
                    1e-6f
                )
                < 0.10f
        ) {
            continue;
        }

        bool duplicate =
            false;

        for (
            const auto& existing :
            tracks
        ) {
            if (
                tracksDuplicate(
                    track,
                    existing
                )
            ) {
                duplicate =
                    true;
                break;
            }
        }

        if (!duplicate) {
            tracks.push_back(
                std::move(
                    track
                )
            );
        }
    }

    std::sort(
        tracks.begin(),
        tracks.end(),
        [](const Track& a,
           const Track& b) {
            return
                a.score
                > b.score;
        }
    );

    if (
        static_cast<int>(
            tracks.size()
        )
        > args.maxCandidates
    ) {
        tracks.resize(
            args.maxCandidates
        );
    }

    auto passEnd =
        std::chrono::steady_clock::now();

    double seconds =
        std::chrono::duration<double>(
            passEnd
            - passStart
        ).count();

    std::cout << "    integrity reject: " << invalidTrajectories
              << " | path-null tested: " << nullControlTested
              << " | path-null rejected: " << nullControlRejected << "\n";

    std::cout
        << "    pass candidates: "
        << tracks.size()
        << "\n"
        << "    pass runtime: "
        << std::fixed
        << std::setprecision(2)
        << seconds
        << " s\n";

    return tracks;
}

static void printFinalCandidates(
    const std::vector<Track>& tracks,
    const std::vector<ImageMeta>& images
) {
    std::cout
        << "\n\n############################################################\n"
        << "FINAL DEEP SEARCH RESULTS\n"
        << "############################################################\n";

    int highCount = 0;
    int mediumCount = 0;

    for (
        const auto& track :
        tracks
    ) {
        if (
            track.priority
            == "HIGH"
        ) {
            ++highCount;
        }
        else if (
            track.priority
            == "MEDIUM"
        ) {
            ++mediumCount;
        }
    }

    std::cout
        << "HIGH priority: "
        << highCount
        << "\n"
        << "MEDIUM priority: "
        << mediumCount
        << "\n"
        << "Total retained: "
        << tracks.size()
        << "\n\n";

    int display =
        static_cast<int>(
            tracks.size()
        );

    for (
        int i = 0;
        i < display;
        ++i
    ) {
        const auto& track =
            tracks[i];

        std::cout
            << "C"
            << std::setw(2)
            << std::setfill('0')
            << (i + 1)
            << std::setfill(' ')
            << " ["
            << track.priority
            << "]"
            << " | pass="
            << track.passName
            << " | frames="
            << track.points.size()
            << " | speed="
            << std::fixed
            << std::setprecision(2)
            << track.speed
            << " px/h"
            << " | vx="
            << track.vx
            << " | vy="
            << track.vy
            << " | RMS="
            << track.rms
            << " | brightnessCV="
            << track.brightnessCv
            << " | sunward="
            << track.sunwardRate
            << " px/h"
            << " | score="
            << track.score
            << "\n";

        if (
            !track.points.empty()
        ) {
            const auto& first =
                track.points.front();

            const auto& last =
                track.points.back();

            std::cout
                << "    first: "
                << images[
                    first.frame
                ].name
                << " ("
                << first.x
                << ", "
                << first.y
                << ")\n";

            std::cout
                << "    last : "
                << images[
                    last.frame
                ].name
                << " ("
                << last.x
                << ", "
                << last.y
                << ")\n";
        }
    }
}

static void printGpuInfo() {
    int count = 0;

    CUDA_CHECK(
        cudaGetDeviceCount(
            &count
        )
    );

    if (count < 1) {
        throw std::runtime_error(
            "No CUDA GPU detected"
        );
    }

    cudaDeviceProp props{};

    CUDA_CHECK(
        cudaGetDeviceProperties(
            &props,
            0
        )
    );

    std::cout
        << "GPU: "
        << props.name
        << "\n"
        << "GPU memory: "
        << std::fixed
        << std::setprecision(1)
        << props.totalGlobalMem
            / (
                1024.0
                * 1024.0
                * 1024.0
            )
        << " GiB\n"
        << "Compute capability: "
        << props.major
        << "."
        << props.minor
        << "\n";
}

static Args parseArgs(
    int argc,
    char** argv
) {
    Args args;

    for (
        int i = 1;
        i < argc;
        ++i
    ) {
        std::string option =
            argv[i];

        auto value =
            [&](const char* flag) {
                if (
                    i + 1
                    >= argc
                ) {
                    throw std::runtime_error(
                        std::string(
                            "Missing value after "
                        )
                        + flag
                    );
                }

                return
                    std::string(
                        argv[++i]
                    );
            };

        if (
            option
            == "--camera"
        ) {
            args.camera =
                lowerCopy(
                    value("--camera")
                );
        }
        else if (
            option
            == "--resolution"
        ) {
            args.resolution =
                lowerCopy(
                    value("--resolution")
                );
        }
        else if (
            option
            == "--input-dir"
        ) {
            args.inputDir =
                value("--input-dir");
        }
        else if (
            option
            == "--budget-minutes"
        ) {
            args.budgetMinutes =
                std::stoi(
                    value(
                        "--budget-minutes"
                    )
                );
        }
        else if (
            option
            == "--min-frames"
        ) {
            args.minFrames =
                std::stoi(
                    value("--min-frames")
                );
        }
        else if (
            option
            == "--edge-mask"
        ) {
            args.edgeMask =
                std::stoi(
                    value("--edge-mask")
                );
        }
        else if (
            option
            == "--center-mask-frac"
        ) {
            args.centerMaskFraction =
                std::stof(
                    value(
                        "--center-mask-frac"
                    )
                );
        }
        else if (option == "--within-window-stationary-filter") {
            args.withinWindowStationaryFilter = true;
        }
        else if (option == "--stationary-fraction") {
            args.stationaryMinFrameFraction = std::stof(value("--stationary-fraction"));
        }
        else if (option == "--stationary-bin") {
            args.stationaryBin = std::stoi(value("--stationary-bin"));
        }
        else if (option == "--trajectory-integrity-filter") {
            args.trajectoryIntegrityFilter = true;
        }
        else if (option == "--trajectory-null-control") {
            args.trajectoryNullControl = true;
        }
        else if (option == "--spatial-balanced-peaks") {
            args.spatialBalancedPeaks = true;
        }
        else if (
            option
            == "--max-candidates"
        ) {
            args.maxCandidates =
                std::stoi(
                    value(
                        "--max-candidates"
                    )
                );
        }
        else if (
            option == "--help"
            ||
            option == "-h"
        ) {
            std::cout
                << "Comet Hunter Archive CUDA v7\n\n"
                << "Usage:\n"
                << "  ./comet_hunter_deep --camera c3 --budget-minutes 60\n\n"
                << "Options:\n"
                << "  --camera c2|c3\n"
                << "  --resolution auto|512|1024\n"
                << "  --input-dir /dev/shm/...  (use staged archive images)\n"
                << "  --budget-minutes 60\n"
                << "  --min-frames 5\n"
                << "  --edge-mask 24\n"
                << "  --center-mask-frac 0.09\n"
                << "  --max-candidates 100\n"
                << "  --within-window-stationary-filter (experimental, OFF by default)\n"
                << "  --stationary-fraction 0.80 (fraction of frames)\n"
                << "  --stationary-bin 2 (spatial pixel bin)\n"
                << "  --trajectory-integrity-filter (experimental; endpoint fit coherence)\n"
                << "  --trajectory-null-control (experimental; shifted-path negative control)\n"
                << "  --spatial-balanced-peaks (experimental; brightness + spatial distribution)\n";

            std::exit(0);
        }
        else {
            throw std::runtime_error(
                "Unknown option: "
                + option
            );
        }
    }

    if (
        args.camera != "c2"
        &&
        args.camera != "c3"
    ) {
        throw std::runtime_error(
            "--camera must be c2 or c3"
        );
    }

    if (
        args.resolution != "auto"
        &&
        args.resolution != "512"
        &&
        args.resolution != "1024"
    ) {
        throw std::runtime_error(
            "--resolution must be auto, 512, or 1024"
        );
    }

    if (args.stationaryBin < 1 || args.stationaryBin > 8 ||
        !std::isfinite(args.stationaryMinFrameFraction) ||
        args.stationaryMinFrameFraction < 0.5f ||
        args.stationaryMinFrameFraction > 1.0f) {
        throw std::runtime_error("Invalid within-window stationary filter parameters");
    }

    if (
        args.budgetMinutes
        < 1
    ) {
        args.budgetMinutes = 1;
    }

    return args;
}

int main(
    int argc,
    char** argv
) {
    try {
        Args args =
            parseArgs(
                argc,
                argv
            );

        std::cout
            << "============================================================\n"
            << "Comet Hunter Native CUDA v7 ARCHIVE ENGINE\n"
            << "Multi-pass A100 search with recurring-artifact rejection\n"
            << "Temporary source data lives in /dev/shm only\n"
            << "============================================================\n";

        if (
            !fs::exists(
                "/dev/shm"
            )
        ) {
            throw std::runtime_error(
                "/dev/shm is unavailable"
            );
        }

        printGpuInfo();

        RamWorkspace workspace;

        auto globalStart =
            std::chrono::steady_clock::now();

        std::vector<ImageMeta> images;

        if (
            !args.inputDir.empty()
        ) {
            std::cout
                << "Using staged archive directory: "
                << args.inputDir
                << "\n";

            images =
                collectImagesFromDir(
                    args,
                    fs::path(args.inputDir)
                );
        }
        else {
            fetchAndExtract(
                args,
                workspace
            );

            images =
                collectImages(
                    args,
                    workspace
                );
        }

        if (
            images.size()
            < static_cast<size_t>(
                args.minFrames
            )
        ) {
            throw std::runtime_error(
                "Too few usable images"
            );
        }

        std::cout
            << "Usable frames: "
            << images.size()
            << "\n"
            << "Compute budget: "
            << args.budgetMinutes
            << " minutes\n";

        std::vector<Track>
            globalTracks;

        int passLevel = 0;

        while (true) {
            auto now =
                std::chrono::steady_clock::now();

            double elapsedMinutes =
                std::chrono::duration<double>(
                    now
                    - globalStart
                ).count()
                / 60.0;

            if (
                passLevel > 0
                &&
                elapsedMinutes
                    >= args.budgetMinutes
            ) {
                break;
            }

            PassParams pass =
                makePass(
                    passLevel,
                    args
                );

            std::vector<Track>
                tracks =
                    runPass(
                        images,
                        args,
                        pass
                    );

            mergeTracks(
                globalTracks,
                std::move(
                    tracks
                ),
                args.maxCandidates
            );

            auto afterPass =
                std::chrono::steady_clock::now();

            double totalMinutes =
                std::chrono::duration<double>(
                    afterPass
                    - globalStart
                ).count()
                / 60.0;

            std::cout
                << "\nAccumulated unique candidates: "
                << globalTracks.size()
                << "\n"
                << "Total elapsed: "
                << std::fixed
                << std::setprecision(2)
                << totalMinutes
                << " min / "
                << args.budgetMinutes
                << " min budget\n";

            ++passLevel;

            if (
                passLevel >= 40
            ) {
                std::cout
                    << "Reached maximum 40 unique deep-search passes.\n";
                break;
            }
        }

        printFinalCandidates(
            globalTracks,
            images
        );

        std::error_code ec;
        fs::remove_all(
            workspace.root() / "images",
            ec
        );

        auto globalEnd =
            std::chrono::steady_clock::now();

        double totalMinutes =
            std::chrono::duration<double>(
                globalEnd
                - globalStart
            ).count()
            / 60.0;

        std::cout
            << "\nDeep search finished after "
            << std::fixed
            << std::setprecision(2)
            << totalMinutes
            << " minutes.\n"
            << "All temporary SOHO images removed from /dev/shm.\n";

        return 0;
    }
    catch (
        const std::exception& error
    ) {
        std::cerr
            << "Fatal: "
            << error.what()
            << "\n";

        return 1;
    }
}
