# SOHO Comet Hunter

SOHO Comet Hunter is an experimental CUDA-accelerated pipeline for searching historical SOHO/LASCO imagery for faint moving comet candidates.

## Live dashboard

The public candidate-review dashboard is available here:

**https://greatn8.github.io/soho-comet-hunter/**

The dashboard shows verified candidate events produced by the pipeline, including zoomed annotated videos and available event metadata such as pipeline score, review class, motion, RMS, frame count, and observation time.

## What the pipeline does

The system processes SOHO/LASCO image sequences, detects moving features, links detections across frames, fits trajectories, ranks candidate events, and produces higher-resolution verification videos for human review.

The current pipeline uses classical image processing, motion tracking, trajectory fitting, and rule-based ranking accelerated with CUDA. It does not currently use a neural network for candidate detection.

## Important note

A dashboard entry is a **candidate for review**, not a confirmed comet discovery.

Some detections may be stars, image artifacts, detector effects, cosmic-ray events, or other non-comet features. Candidates should be independently checked against the original SOHO imagery and known-object records before any discovery claim is made.

## Data source

Imagery used by this project comes from the NASA/ESA SOHO mission and the LASCO coronagraph archive.

## Project status

The pipeline is under active development. New verified candidate events can be published automatically to the dashboard as the historical archive search progresses.
