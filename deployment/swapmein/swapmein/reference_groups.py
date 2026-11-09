"""Photo groups are appearance evidence, never additional source people."""
from .errors import PublicError

MAX_REFERENCE_IMAGES = 6


def group_indexes(target, maximum=MAX_REFERENCE_IMAGES):
    primary = target.get('reference_index', 0)
    group = target.get('reference_indexes')
    group = [primary] if group is None else group
    if (type(primary) is not int or not isinstance(group, list) or not 1 <= len(group) <= MAX_REFERENCE_IMAGES
            or any(type(index) is not int or not 0 <= index < maximum for index in group)
            or group[0] != primary or len(set(group)) != len(group)):
        raise PublicError('Assign a valid photo group with the primary photo first.')
    return group


def reference_words(indexes):
    if len(indexes) == 1:
        return f'reference image {indexes[0] + 1}'
    return 'reference images ' + ', '.join(str(index + 1) for index in indexes)


def picture_words(indexes):
    return ', '.join(f'<Picture {index + 1}>' for index in indexes)


def kling_words(indexes):
    return ', '.join(f'@Image{index + 1}' for index in indexes)


def angle_guidance(indexes, *, kling=False, structured=False):
    if len(indexes) <= 1:
        return ''
    describe = kling_words if kling else picture_words if structured else reference_words
    return (f'{describe(indexes)} show different views of the same replacement character, not separate people. '
            f'Use {describe(indexes[:1])} as the primary appearance, hairstyle and clothing reference; '
            f'use {describe(indexes[1:])} to clarify that same character\'s facial identity and geometry from other angles. '
            'Do not copy a supporting photo\'s pose, lighting, background or conflicting costume, and do not add extra people.')


def setup_groups(setup):
    setup = setup if isinstance(setup, dict) else setup.model_dump()
    targets = setup.get('targets')
    if targets:
        return [group_indexes(target) for target in targets]
    return [group_indexes({'reference_index':0, 'reference_indexes':setup.get('reference_indexes')})]


def reference_count(setup):
    return len({index for group in setup_groups(setup) for index in group})


def has_multiple_angles(setup):
    return any(len(group) > 1 for group in setup_groups(setup))
