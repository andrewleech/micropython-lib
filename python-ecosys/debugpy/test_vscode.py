"""Test script for VS Code debugging with MicroPython debugpy."""

import sys

sys.path.insert(0, ".")

import debugpy

foo = 42
bar = "Hello, MicroPython!"


def fibonacci(n):
    """Calculate fibonacci number (iterative for efficiency)."""
    if n <= 1:
        return n
    a, b = 0, 1
    for _ in range(2, n + 1):
        a, b = b, a + b
    return b


def debuggable_code():
    """The actual code we want to debug - wrapped in a function so sys.settrace will trace it."""
    global foo
    print("Starting debuggable code...")

    # Test data - set breakpoint here (using smaller numbers to avoid slow fibonacci)
    numbers = [3, 4, 5]
    for i, num in enumerate(numbers):
        print(f"Calculating fibonacci({num})...")
        result = fibonacci(num)  # <-- SET BREAKPOINT HERE
        foo += result  # Modify foo to see if it gets traced
        print(f"fibonacci({num}) = {result}")
        print(sys.implementation)
        import machine

        print(dir(machine))

    # Test manual breakpoint
    print("\nTriggering manual breakpoint...")
    debugpy.breakpoint()
    print("Manual breakpoint triggered!")

    print("Test completed successfully!")


def main():
    print("MicroPython VS Code Debugging Test")
    print("==================================")

    # Start debug server
    try:
        host, port = debugpy.listen()
        print("Debug server listening on {}:{}".format(host, port))
        print("Attach a DAP client now; this will wait for it.")

        # Enable debugging for this thread
        debugpy.debug_this_thread()

        # Block until the client has attached and sent configurationDone, so
        # breakpoints it set are already in place when the traced code runs.
        # A sleep here would be a race the client usually loses.
        if not debugpy.wait_for_client():
            print("No client configured a session; running untraced.")

        # Call the debuggable code function so it gets traced
        debuggable_code()

    except KeyboardInterrupt:
        print("\nTest interrupted by user")
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    main()
