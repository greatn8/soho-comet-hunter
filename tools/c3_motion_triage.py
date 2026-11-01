"""C3 review and notification policy. No speed floor or raw-track rejection."""
import math


def triage(row):
    def finite(value):
        try:
            number = float(value)
            return number if math.isfinite(number) else 0.0
        except (ValueError, TypeError):
            return 0.0

    pixel = row.get('pixel_verification') or {}
    temporal = pixel.get('temporal_motion') or {}
    star = row.get('image_star_motion') or {}
    family = row.get('star_field') or {}
    if star.get('status') == 'STAR_LIKE' or family.get('status') == 'STAR_LIKE':
        return {'stage': 'STAR_LIKE_REVIEW', 'reason': 'Common field motion; retain for optional inspection'}
    if row.get('trajectory_warning') or row.get('quality_issues'):
        return {'stage': 'SECONDARY_REVIEW', 'reason': 'Trajectory quality unresolved'}
    supported = (pixel.get('status') == 'STRONG'
                 and finite(pixel.get('hits')) >= 5
                 and finite(pixel.get('hit_fraction')) >= .6
                 and finite(pixel.get('longest_hit_run')) >= 4
                 and temporal.get('status') == 'MEASURED'
                 and finite(temporal.get('tested')) >= 3
                 and finite(temporal.get('supported')) >= 2
                 and .5 <= finite(temporal.get('support_fraction')) <= 1
                 and finite(temporal.get('supported')) <= finite(temporal.get('tested'))
                 and finite(temporal.get('supported')) / max(1, finite(temporal.get('tested'))) >= .5)
    if supported:
        return {'stage': 'MOTION_SUPPORTED_REVIEW',
                'reason': 'Temporal source evidence passes; different motion from stars still unverified'}
    return {'stage': 'SECONDARY_REVIEW', 'reason': 'Independent temporal source evidence insufficient or unavailable'}


def protected(row):
    return bool(row.get('known_object') or row.get('report_id') or
                row.get('status') == 'KNOWN_REPORT' or row.get('realtime_status') == 'KNOWN_REPORT' or
                row.get('review_state') == 'KNOWN_OBJECT' or row.get('review_locked') or
                row.get('starred') or row.get('star_locked'))


def assessed(row):
    """Missing historical evidence is unassessed, never a passing recovery."""
    pixel = row.get('pixel_verification') or {}
    temporal = pixel.get('temporal_motion') or {}
    return (temporal.get('status') == 'MEASURED' and
            isinstance(temporal.get('tested'), (int, float)) and temporal['tested'] >= 3)


def motion_supported(row):
    return triage(row)['stage'] == 'MOTION_SUPPORTED_REVIEW'


def primary_review(row):
    """Preserve known/manually protected objects without claiming a test pass."""
    return protected(row) or (row.get('review_class') == 'STRONG_REVIEW' and motion_supported(row))
