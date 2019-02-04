from secml.array import CArray
from secml.ml.classifiers.loss import CLossHinge
from secml.ml.classifiers.gradients import CClassifierGradient


class CClassifierGradientSVM(CClassifierGradient):
    class_type = 'svm'

    def __init__(self):
        self._loss = CLossHinge()

    def _s(self, clf, tol=1e-6):
        """Indices of margin support vectors."""
        s = clf.alpha.find(
            (abs(clf.alpha) >= tol) *
            (abs(clf.alpha) <= clf.C - tol))
        return CArray(s)

    def _xs(self, clf):
        s = self._s(clf)

        if s.size == 0:
            return None

        xs = clf.sv[s, :].atleast_2d()
        return xs, s

    def hessian(self, clf):
        """
        Compute hessian of the loss w.r.t. the classifier parameters
        """
        svm = clf

        xs, sv_idx = self._xs(clf)  # these points are already normalized

        s = xs.shape[0]

        H = CArray.ones(shape=(s + 1, s + 1))
        H[:s, :s] = svm.kernel.k(xs)
        H[-1, -1] = 0

        return H

    def fd_params(self, x, clf):
        """
        Derivative of the discriminant function w.r.t. the classifier
        parameters
        """
        xs, sv_idx = self._xs()  # these points are already normalized

        if xs is None:
            self.logger.debug("Warning: xs is empty "
                              "(all points are error vectors).")
            return None

        x = x if clf.preprocess is None else clf.preprocess.normalize(x)

        s = xs.shape[0]
        k = x.shape[0]

        Kks_ext = CArray.ones(shape=(k, s + 1))
        Kks_ext[:, :s] = clf.kernel.k(x, xs)
        return Kks_ext

    def fd_x(self, x=None, y=1):
        """
        Derivative of the discriminant function w.r.t. an input sample
        """
        pass

    def L_tot_d_params(self, x, y, clf):
        raise NotImplementedError()
