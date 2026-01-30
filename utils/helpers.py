import random

def generate_versus_pairs(players):
    ids = list(players.keys())
    random.shuffle(ids)
    pairs = []
    while len(ids) >= 2:
        p1 = ids.pop()
        p2 = ids.pop()
        pairs.append((p1, p2))
    return pairs
