import sys
try:
    raise ValueError('bad input')
except Exception as e:
    sys.stderr.write('synthetic guard error (%s); refused\n' % type(e).__name__)
    sys.exit(2)
