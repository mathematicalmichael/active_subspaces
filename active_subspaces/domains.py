import numpy as np
import warnings
from utils.utils import process_inputs
from scipy.spatial import ConvexHull
from utils.qp_solver import QPSolver

class ActiveVariableDomain():
    vertY, vertX = None, None
    convhull, constraints = None, None
    
class UnboundedActiveVariableDomain(ActiveVariableDomain):
    
    def __init__(self, subspaces):
        self.subspaces = subspaces
        self.m, self.n = subspaces.W1.shape

class BoundedActiveVariableDomain(ActiveVariableDomain):
    
    def __init__(self, subspaces):
        self.subspaces = subspaces
        W1 = subspaces.W1
        m, n = W1.shape
        
        if n == 1:
            Y, X = interval_endpoints(W1)
            convhull = None
            constraints = None
        else:
	    Y, X = zonotope_vertices(W1)
	    numverts = nzv(m,n)[0]
	    if Y.shape[0] != numverts:
	        warnings.warn('Number of zonotope vertices should be %d but is %d' % (numverts,Y.shape[0]))
	    
            convhull = ConvexHull(Y)
            A = convhull.equations[:,:n]
            b = convhull.equations[:,n]
            constraints = ({'type' : 'ineq',
                        'fun' : lambda x: np.dot(A, x) - b,
                        'jac' : lambda x: A})

        # store variables
        self.m, self.n = m, n
        self.vertY, self.vertX = Y, X
        self.convhull, self.constraints = convhull, constraints

class ActiveVariableMap():
    def __init__(self, domain):
        self.domain = domain

    def forward(self, X):
        X = process_inputs(X)[0]
        W1, W2 = self.domain.subspaces.W1, self.domain.subspaces.W2
        return np.dot(X, W1), np.dot(X, W2)

    def inverse(self, Y, N=1):
        # check inputs
        Y = process_inputs(Y)[0]
        if type(N) is not int:
            raise TypeError('N must be an int') 
        
        Z = self.regularize_z(Y, N)
        W = self.domain.subspaces.eigenvectors
        return rotate_x(Y, Z, W)

    def regularize_z(self, Y, N):
        raise NotImplementedError()

class BoundedActiveVariableMap(ActiveVariableMap):

    def regularize_z(self, Y, N):
        W1, W2 = self.domain.subspaces.W1, self.domain.subspaces.W2
        m, n = W1.shape

        # sample the z's
        # TODO: preallocate and organize properly
        NY = Y.shape[0]
        Zlist = []
        for y in Y:
            Zlist.append(sample_z(N, y, W1, W2))
        return np.array(Zlist).reshape((NY, m-n, N))

class UnboundedActiveVariableMap(ActiveVariableMap):

    def regularize_z(self, Y, N):
        m, n = self.domain.subspaces.W1.shape

        # sample z's
        NY = Y.shape[0]
        return np.random.normal(size=(NY, m-n, N))

def nzv(m, n, M=None):
    # number of zonotope vertices
    if M is None:
        M = np.zeros((m, n))
    if m==1 or n==1:
        M[m-1, n-1] = 2
    elif M[m-1, n-1]==0:
        k1, M = nzv(m-1, n-1, M)
        k2, M = nzv(m-1, n, M)
        M[m-1, n-1] = k1 + k2
        for i in range(n-1):
            M = nzv(m, i+1, M)[1]
    k = M[m-1, n-1]
    return k, M

def interval_endpoints(W1):
    m = W1.shape[0]
    y0 = np.dot(W1.T, np.sign(W1))[0]
    if y0 < -y0:
        yl, yu = y0, -y0
        xl, xu = np.sign(W1), -np.sign(W1)
    else:
        yl, yu = -y0, y0
        xl, xu = -np.sign(W1), np.sign(W1)
    Y = np.array([yl, yu]).reshape((2,1))
    X = np.vstack((xl.reshape((1, m)), xu.reshape((1, m))))
    return Y, X

def zonotope_vertices(W1, NY=10000):
    m, n = W1.shape
    
    Xlist = []
    nzv = 0
    for i in range(NY):
        y = np.random.normal(size=(n))
        x = np.sign(np.dot(y, W1.transpose()))
        addx = True
        for xx in Xlist:
            if all(x==xx):
                addx = False
                break
        if addx:
            Xlist.append(x)
            nzv += 1
    X = np.array(Xlist).reshape((nzv, m))
    Y = np.dot(X, W1).reshape((nzv, n))
    return Y, X

def sample_z(N, y, W1, W2):
    m, n = W1.shape
    qps = QPSolver()
    
    s = np.dot(W1, y).reshape((m, 1))
    if np.all(np.zeros((m, 1)) <= 1-s) and np.all(np.zeros((m, 1)) >= -1-s):
        z0 = np.zeros((m-n, 1))
    else:
        lb = -np.ones((m,1))
        ub = np.ones((m,1))
        c = np.zeros((m,1))
        x0 = qps.linear_program_eq(c, W1.T, y.reshape((n,1)), lb, ub)
        z0 = np.dot(W2.T, x0).reshape((m-n, 1))

    # get MCMC step size
    sig = 0.1*np.minimum(
            np.linalg.norm(np.dot(W2, z0) + s - 1),
            np.linalg.norm(np.dot(W2, z0) + s + 1))

    # burn in
    for i in range(10*N):
        zc = z0 + sig*np.random.normal(size=z0.shape)
        if np.all(np.dot(W2, zc) <= 1-s) and np.all(np.dot(W2, zc) >= -1-s):
            z0 = zc

    # sample
    Z = np.zeros((m-n, N))
    for i in range(N):
        zc = z0 + sig*np.random.normal(size=z0.shape)
        if np.all(np.dot(W2, zc) <= 1-s) and np.all(np.dot(W2, zc) >= -1-s):
            z0 = zc
        Z[:,i] = z0.reshape((z0.shape[0], ))

    return Z

def rotate_x(Y, Z, W):
    NY, n = Y.shape
    N = Z.shape[2]
    m = n + Z.shape[1]

    YY = np.tile(Y.reshape((NY, n, 1)), (1, 1, N))
    YZ = np.concatenate((YY, Z), axis=1).transpose((1, 0, 2)).reshape((m, N*NY)).transpose((1, 0))
    X = np.dot(YZ, W.T).reshape((N*NY,m))
    ind = np.kron(np.arange(NY), np.ones(N)).reshape((N*NY,1))
    return X, ind
