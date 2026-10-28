"""
Damage Severity Classification Utility
Vehicle Damage Detection & Repair Cost Estimation
"""

from typing import List, Dict, Any, Tuple


# Cost multipliers as specified: Minor x0.5, Moderate x1.0, Severe x1.8
SEVERITY_MULTIPLIERS = {
    'Minor': 0.5,
    'Moderate': 1.0,
    'Severe': 1.8
}

SEVERITY_COLORS = {
    'Minor': '#10B981',    # Emerald Green
    'Moderate': '#F59E0B', # Amber Yellow/Orange
    'Severe': '#EF4444'    # Crimson Red
}

SEVERITY_BADGE_CLASSES = {
    'Minor': 'badge-minor',
    'Moderate': 'badge-moderate',
    'Severe': 'badge-severe'
}

DEFAULT_PART_NAMES = ['Bonnet', 'Bumper', 'Dickey', 'Door', 'Fender', 'Light', 'Windshield']


def get_part_name(class_id: int, class_names: List[str] = None) -> str:
    """Map class ID to human-readable part name."""
    names = class_names or DEFAULT_PART_NAMES
    try:
        idx = int(class_id)
        if 0 <= idx < len(names):
            return names[idx]
    except (ValueError, TypeError):
        pass
    return f"Part-{class_id}"


def calculate_box_metrics(xyxy: List[float], img_w: int, img_h: int) -> Tuple[float, float]:
    """
    Calculate bounding box area and relative area ratio to full image.
    """
    x1, y1, x2, y2 = xyxy
    box_w = max(0.0, float(x2 - x1))
    box_h = max(0.0, float(y2 - y1))
    box_area = box_w * box_h
    total_img_area = max(1.0, float(img_w * img_h))
    area_ratio = box_area / total_img_area
    return box_area, area_ratio


def boxes_intersect_or_overlap(boxA: List[float], boxB: List[float], min_overlap_ratio: float = 0.05) -> bool:
    """
    Check if two bounding boxes intersect significantly (Intersection over Min-Area or IoU).
    """
    xA = max(boxA[0], boxB[0])
    yA = max(boxA[1], boxB[1])
    xB = min(boxA[2], boxB[2])
    yB = min(boxA[3], boxB[3])

    inter_w = max(0.0, xB - xA)
    inter_h = max(0.0, yB - yA)
    inter_area = inter_w * inter_h

    if inter_area <= 0:
        return False

    areaA = max(1.0, (boxA[2] - boxA[0]) * (boxA[3] - boxA[1]))
    areaB = max(1.0, (boxB[2] - boxB[0]) * (boxB[3] - boxB[1]))

    # If intersection occupies > 5% of either box, consider it an overlap
    ratioA = inter_area / areaA
    ratioB = inter_area / areaB
    return (ratioA >= min_overlap_ratio) or (ratioB >= min_overlap_ratio)


def compute_overlap_counts(boxes_coords: List[List[float]]) -> List[int]:
    """
    For each bounding box, count how many other bounding boxes it overlaps with.
    """
    n = len(boxes_coords)
    overlap_counts = [0] * n
    for i in range(n):
        for j in range(i + 1, n):
            if boxes_intersect_or_overlap(boxes_coords[i], boxes_coords[j]):
                overlap_counts[i] += 1
                overlap_counts[j] += 1
    return overlap_counts


def classify_severity(area_ratio: float, confidence: float, overlap_count: int) -> Tuple[str, float]:
    """
    Classify detection as Minor, Moderate, or Severe based on:
      1. Bbox Area Ratio (size relative to image)
      2. Model Confidence
      3. Overlap Count (presence of compound/clustered damage)
    
    Returns:
      (severity_label, cost_multiplier)
    """
    is_compound = overlap_count >= 2
    is_high_area = area_ratio >= 0.12
    is_medium_area = 0.04 <= area_ratio < 0.12
    
    # Severe criteria:
    # - Clustered damage (overlap_count >= 2)
    # - OR Large area deformation (area_ratio >= 0.12)
    # - OR Medium-large damage with overlap and confident detection
    if is_compound or is_high_area or (area_ratio >= 0.08 and overlap_count >= 1) or (area_ratio >= 0.06 and overlap_count >= 1 and confidence >= 0.70):
        severity = 'Severe'
    # Moderate criteria:
    # - Medium area damage (>= 0.04)
    # - OR Any single overlapping damage
    # - OR High confidence detection with small/moderate area
    elif is_medium_area or overlap_count == 1 or (confidence >= 0.72 and area_ratio >= 0.02):
        severity = 'Moderate'
    else:
        severity = 'Minor'

    multiplier = SEVERITY_MULTIPLIERS[severity]
    return severity, multiplier


def extract_and_classify_detections(yolo_result, class_names: List[str] = None) -> List[Dict[str, Any]]:
    """
    Extract boxes from a YOLOv8 Results object, calculate area ratio,
    overlap counts, and assign severity classifications and multipliers.
    """
    if yolo_result is None or not hasattr(yolo_result, 'boxes') or len(yolo_result.boxes) == 0:
        return []

    boxes = yolo_result.boxes
    orig_shape = getattr(yolo_result, 'orig_shape', (640, 640))
    img_h, img_w = orig_shape[0], orig_shape[1]

    # Collect coordinates
    raw_boxes = []
    for box in boxes:
        cls_id = int(box.cls.item())
        conf = float(box.conf.item())
        xyxy = box.xyxy[0].tolist() if hasattr(box.xyxy, '__len__') else []
        raw_boxes.append({
            'cls_id': cls_id,
            'conf': conf,
            'xyxy': xyxy
        })

    # Compute overlaps
    coords_list = [b['xyxy'] for b in raw_boxes]
    overlaps = compute_overlap_counts(coords_list)

    detections = []
    for i, b in enumerate(raw_boxes):
        _, area_ratio = calculate_box_metrics(b['xyxy'], img_w, img_h)
        severity, multiplier = classify_severity(area_ratio, b['conf'], overlaps[i])
        part_name = get_part_name(b['cls_id'], class_names)

        detections.append({
            'index': i + 1,
            'class_id': b['cls_id'],
            'part_name': part_name,
            'confidence': round(b['conf'], 3),
            'confidence_percent': round(b['conf'] * 100, 1),
            'area_ratio': round(area_ratio, 4),
            'area_percentage': round(area_ratio * 100, 2),
            'overlap_count': overlaps[i],
            'severity': severity,
            'multiplier': multiplier,
            'color': SEVERITY_COLORS[severity],
            'badge_class': SEVERITY_BADGE_CLASSES[severity],
            'bbox': [round(x, 1) for x in b['xyxy']]
        })

    return detections


def calculate_pricing_with_severity(
    classified_detections: List[Dict[str, Any]],
    car_brand: str,
    car_model: str,
    db_connection,
    default_price: float = 200.0
) -> Dict[str, Any]:
    """
    Calculate base price and multiplier-adjusted repair prices for all classified detections.
    
    Returns:
      {
         'items': [ {...}, ... ],
         'summary_by_part': { part: {count, base_price, total_cost, severities} },
         'total_cost': float,
         'severity_counts': {'Minor': int, 'Moderate': int, 'Severe': int}
      }
    """
    if not classified_detections:
        return {
            'items': [],
            'summary_by_part': {},
            'total_cost': 0.0,
            'severity_counts': {'Minor': 0, 'Moderate': 0, 'Severe': 0}
        }

    # Fetch base prices for brand & model from DB
    base_prices = {}
    if db_connection:
        try:
            with db_connection.cursor(dictionary=True) as cursor:
                cursor.execute(
                    "SELECT part, price FROM car_models WHERE brand = %s AND model = %s",
                    (car_brand, car_model)
                )
                rows = cursor.fetchall()
                for r in rows:
                    base_prices[r['part']] = float(r['price'])
        except Exception as e:
            print(f"Error loading part prices: {e}")

    items = []
    total_cost = 0.0
    severity_counts = {'Minor': 0, 'Moderate': 0, 'Severe': 0}
    summary_by_part = {}

    for det in classified_detections:
        part = det['part_name']
        base_price = base_prices.get(part, default_price)
        final_price = round(base_price * det['multiplier'], 2)
        total_cost += final_price

        sev = det['severity']
        severity_counts[sev] = severity_counts.get(sev, 0) + 1

        item_data = dict(det)
        item_data['base_price'] = base_price
        item_data['final_price'] = final_price
        item_data['is_fallback_price'] = (part not in base_prices)
        items.append(item_data)

        # Aggregate summary by part
        if part not in summary_by_part:
            summary_by_part[part] = {
                'part_name': part,
                'count': 0,
                'base_price': base_price,
                'total_cost': 0.0,
                'severities': []
            }
        summary_by_part[part]['count'] += 1
        summary_by_part[part]['total_cost'] += final_price
        summary_by_part[part]['severities'].append(sev)

    # Format part summaries
    for part, data in summary_by_part.items():
        data['total_cost'] = round(data['total_cost'], 2)

    return {
        'items': items,
        'summary_by_part': summary_by_part,
        'total_cost': round(total_cost, 2),
        'severity_counts': severity_counts
    }
