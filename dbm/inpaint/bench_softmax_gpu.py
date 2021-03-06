import theano
from pylearn2.utils import sharedX
import numpy as np
import time

# Remove a simplifier that would thwart our attempts to control what goes in the graph
canonizer = theano.tensor.opt.local_mul_canonizer
found = False
for idx, entry in enumerate(canonizer.external_simplifiers):
    if entry[0] == 'softmax_simplifier':
        found = True
        break
assert found
del canonizer.external_simplifiers[idx]

def bench(f, m, n):
    #print f

    rng = np.random.RandomState([2012,9,11])

    X = sharedX(rng.randn(m,n))
    Y = sharedX(X.get_value())

    func = theano.function([], updates = { Y : f(X) })

    nodes = func.maker.fgraph.toposort()


    # Make sure the optimizations haven't made us benchmark something different from what we intend
    if f is my_softmax:
        try:
            assert True not in [ isinstance(node.op, theano.tensor.nnet.Softmax) for node in nodes ]
            assert True not in [ isinstance(node.op, theano.sandbox.cuda.nnet.GpuSoftmax) for node in nodes ]
            # make sure there's nothing transferring stuff to / from the host
            # assert True not in [ str(node.op).find('ost') != -1 for node in nodes ]
            # make sure stuff is running on the GPU
            assert True in [ str(node.op).startswith('Gpu') for node in nodes ]
        except AssertionError:
            for node in nodes:
               print '\t',node
            raise
    if f is softmax_op:
        assert True in [ isinstance(node.op, theano.sandbox.cuda.nnet.GpuSoftmax) for node in nodes ]
    if f is softmax_with_bias:
        assert True in [ isinstance(node.op, theano.sandbox.cuda.nnet.GpuSoftmaxWithBias) for node in nodes ]

    # warm up
    for i in xrange(5):
        func()

    # actual time
    times = []
    for i in xrange(5):
        t1 = time.time()
        func()
        t2 = time.time()
        times.append(t2-t1)

    rval = np.asarray(times).mean()
    #print rval
    return rval

def my_softmax(X):
    mx = X.max(axis=1)
    safe_X = X - mx.dimshuffle(0,'x')
    unnormalized = theano.tensor.exp(safe_X)
    Z = unnormalized.sum(axis=1)
    normalized = unnormalized / Z.dimshuffle(0,'x')
    return normalized

def softmax_op(X):
    return theano.tensor.nnet.softmax(X)

def softmax_with_bias(X):
    m, n = X.get_value().shape
    zeros = np.zeros( (n,) )
    zeros = sharedX(zeros)
    return theano.tensor.nnet.softmax_with_bias(X,zeros)

funcs = [ my_softmax, softmax_op, softmax_with_bias ]
#funcs = [ my_softmax ]

for m in [1,2,3,4,10,16,32,64,100,128,1000, 280800]:
    for n in [1,2,3,4,7,8,10,16,32,64,100,128,1000]:
        if m == 280800 and n == 1000:
            continue
        print 'm = %d, n = %d' % (m,n)

        times = [ bench(f,m,n) for f in funcs ]

        idx = times.index(min(times))
        assert idx >= 0
        winner = funcs[idx]

        print 'winner is ',winner
        winning_time = times[idx]

        if winner is my_softmax:
            del times[idx]
            if len(times) > 0:
                next_best = min(times)
                speedup = next_best / winning_time
                print 'I win by a factor of',speedup
        else:
            speedup = times[0] / winning_time
            print "beats me by a factor of",speedup
