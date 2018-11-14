def well_coordinates(i: int, j: int):
    return chr(ord('A')+i) + str(j+1)


def coordinates_for(well):
    row = ord(well[0]) - ord('A')
    col = int(well[1:]) - 1
    return row, col
