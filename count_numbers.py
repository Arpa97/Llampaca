def count_numbers(s):
    return sum(1 for char in s if char.isdigit())

# Test cases
print(count_numbers("abc123def"))  # Expected: 3
print(count_numbers("12345"))      # Expected: 5
print(count_numbers("no numbers here"))  # Expected: 0
print(count_numbers(""))  # Expected: 0