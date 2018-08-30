def well_coordinates(i: int, j: int):
    return chr(ord('A')+i) + str(j+1)

def coordinates_for(part_ref):
    row = ord(part_ref[0]) - ord('A')
    col = int(part_ref[1:]) - 1
    return row, col
