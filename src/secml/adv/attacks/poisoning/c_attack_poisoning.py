"""
.. module:: CAttackPoisoning
   :synopsis: Interface for poisoning attacks

.. moduleauthor:: Ambra Demontis <ambra.demontis@diee.unica.it>

"""
import warnings
from abc import ABCMeta, abstractmethod
import six
from six.moves import range

from secml.adv.attacks import CAttack
from secml.optim.optimizers import COptimizer
from secml.array import CArray
from secml.data import CDataset
from secml.ml.classifiers.loss import CLoss
from secml.ml.peval.metrics import CMetric
from secml.optim.constraints import CConstraint
from secml.optim.function import CFunction


@six.add_metaclass(ABCMeta)
class CAttackPoisoning(CAttack):
    """Class for implementing poisoning attacks."""
    __super__ = 'CAttackPoisoning'

    def __init__(self, classifier,
                 training_data,
                 surrogate_classifier,
                 val,
                 surrogate_data=None,
                 distance='l2',
                 dmax=0,
                 lb=0,
                 ub=1,
                 discrete=False,
                 y_target=None,
                 attack_classes='all',
                 solver_type='pgd-ls',
                 solver_params=None,
                 init_type='random',
                 random_seed=None):
        """
        Initialization method.

        It requires classifier, surrogate_classifier, and surrogate_data.
        Note that surrogate_classifier is assumed to be trained (before
        passing it to this class) on surrogate_data.

        TODO: complete list of parameters

        Parameters
        ----------
        discrete: True/False (default: false).
                  If True, input space is considered discrete (integer-valued),
                  otherwise continuous.
        attack_classes: (not supported) list of classes that
                  can be manipulated by the attacker
                  -1 means all classes can be manipulated.
        y_target: could be None, if attack is indiscriminate. For targeted
                  attacks, one can specify one target class towards to which
                  classifying all samples, all specify one label per validation
                  point.
        init_type: String 'random' | 'loss_based', default 'random'
                  Strategy used to chose the initial random samples
        random_seed: seed used to randomly chose the poisoning points
        """

        CAttack.__init__(self, classifier=classifier,
                         surrogate_classifier=surrogate_classifier,
                         surrogate_data=surrogate_data,
                         distance=distance,
                         dmax=dmax,
                         lb=lb,
                         ub=ub,
                         discrete=discrete,
                         y_target=y_target,
                         attack_classes=attack_classes,
                         solver_type=solver_type,
                         solver_params=solver_params)

        if discrete:
            raise ValueError(
                "Poisoning in discrete space is not implemented yet!")

        # fixme: use the cross-entropy for all the classifier poisoning
        # fixme: Bat - WHY!? I don't think we should use a single loss
        if classifier.class_type == 'svm':
            loss_name = 'hinge'
        elif classifier.class_type == 'logistic':
            loss_name = 'log'
        elif classifier.class_type == 'ridge':
            loss_name = 'square'
        else:
            raise NotImplementedError("We cannot poisoning that classifier")

        self._attacker_loss = CLoss.create(
            loss_name)

        self._init_loss = self._attacker_loss

        # hashing xc to avoid re-training clf when xc does not change
        self._xc_hash = None

        self._xc = None  # set of poisoning points along with their labels yc
        self._yc = None
        self._idx = None  # index of the current point to be optimized
        self._training_data = None  # training set used to learn classifier
        self._n_points = None  # FIXME: WHY THIS HAS A SETTER IF NOT AN INIT PARAM?

        # READ/WRITE
        self.val = val  # this is for validation set
        self.training_data = training_data
        self.random_seed = random_seed

        self.init_type = init_type

        # fixme: change this (we needs eta to compute the perturbation if the
        #  attack is performed in a discrete space )
        self.eta = solver_params['eta']

        # this is used to speed up some poisoning algorithms by re-using
        # the solution obtained at a previous step of the optimization
        self._warm_start = None

    ###########################################################################
    #                          READ-WRITE ATTRIBUTES
    ###########################################################################

    @property
    def val(self):
        """Returns the attacker's validation data"""
        return self._val

    @val.setter
    def val(self, value):
        """Sets the attacker's validation data"""
        if value is None:
            self._val = None
            return
        if not isinstance(value, CDataset):
            raise TypeError('val should be a CDataset!')
        self._val = value

    @property
    def training_data(self):
        """Returns the training set used to learn the targeted classifier"""
        return self._training_data

    @training_data.setter
    def training_data(self, value):
        """Sets the training set used to learn the targeted classifier"""
        # mandatory parameter, we raise error also if value is None
        if not isinstance(value, CDataset):
            raise TypeError('training_data should be a CDataset!')
        self._training_data = value

    @property
    def random_seed(self):
        """Returns the attacker's validation data"""
        return self._random_seed

    @random_seed.setter
    def random_seed(self, value):
        """Sets the attacker's validation data"""
        self._random_seed = value

    @property
    def n_points(self):
        """Returns the number of poisoning points."""
        return self._n_points

    @n_points.setter
    def n_points(self, value):
        """Sets the number of poisoning points."""
        if value is None:
            self._n_points = None
            return
        self._n_points = int(value)

    ###########################################################################
    #                              PRIVATE METHODS
    ###########################################################################

    def _constraint_creation(self):

        # only feature increments or decrements are allowed
        lb = self._x0 if self.lb == 'x0' else self.lb
        ub = self._x0 if self.ub == 'x0' else self.ub
        bounds = CConstraint.create('box', lb=lb, ub=ub)

        constr = CConstraint.create(self.distance, center=0, radius=1e12)

        return bounds, constr

    def _init_solver(self):
        """Create solver instance."""

        if self._solver_clf is None or self.discrete is None:
            raise ValueError('Solver not set properly!')

        # map attributes to fun, constr, box
        fun = CFunction(fun=self._objective_function,
                        gradient=self._objective_function_gradient,
                        n_dim=self._classifier.n_features)

        bounds, constr = self._constraint_creation()

        # FIXME: FEW SOLVERS DO NOT SUPPORT DISCRETE. THE FOLLOWING IS A
        #  WORKAROUND TO TRIGGER A PROPER ERROR
        solver_params = self.solver_params
        if self.discrete is True:
            solver_params['discrete'] = True

        self._solver = COptimizer.create(
            self._solver_type,
            fun=fun, constr=constr,
            bounds=bounds,
            **self.solver_params)

        self._solver.verbose = 0
        self._warm_start = None

    def _rnd_init_poisoning_points(
            self, n_points=None, init_from_val=False, val=None):
        """Returns a random set of poisoning points randomly with
        flipped labels."""
        if init_from_val:
            if val:
                init_dataset = val
            else:
                init_dataset = self.val
        else:
            init_dataset = self.surrogate_data

        if init_dataset is None:
            raise ValueError("Surrogate data not set!")
        if (self._n_points is None or self._n_points == 0) and (
                n_points is None or n_points == 0):
            raise ValueError("Number of poisoning points (n_points) not set!")

        if n_points is None:
            n_points = self.n_points

        idx = CArray.randsample(init_dataset.num_samples, n_points,
                                random_state=self.random_seed)

        xc = init_dataset.X[idx, :].deepcopy()

        if not self.discrete:
            # if the attack is in a continuous space we add a
            # little perturbation to the initial poisoning point
            random_noise = CArray.rand(shape=xc.shape,
                                       random_state=self.random_seed)
            xc += 1e-3 * (2 * random_noise - 1)
        else:
            xc = self.add_discrete_perturbation(xc)

        yc = CArray(init_dataset.Y[idx]).deepcopy()  # true labels

        # randomly pick yc from a different class
        for i in range(yc.size):
            labels = CArray.randsample(init_dataset.num_classes, 2,
                                       random_state=self.random_seed)
            if yc[i] == labels[0]:
                yc[i] = labels[1]
            else:
                yc[i] = labels[0]

        return xc, yc

    def _update_poisoned_clf(self, clf=None, tr=None,
                             train_normalizer=False):
        """
        Trains classifier on D (original training data) plus {x,y} (new point).

        Parameters
        ----------
        x: feature vector of new training point
        y: true label of new training point

        Returns
        -------
        clf: trained classifier on D and {x,y}

        """

        #  xc hashing is only valid if clf and tr do not change
        #  (when calling update_poisoned_clf() without parameters)
        xc_hash_is_valid = False
        if clf is None and tr is None:
            xc_hash_is_valid = True

        if clf is None:
            clf = self._solver_clf

        if tr is None:
            tr = self.surrogate_data

        tr = tr.append(CDataset(self._xc, self._yc))

        xc_hash = self._xc.sha1()

        if self._xc_hash is None or self._xc_hash != xc_hash:
            # xc set has changed, retrain clf
            # hash is stored only if update_poisoned_clf() is called w/out pars
            self._xc_hash = xc_hash if xc_hash_is_valid else None
            self._poisoned_clf = clf.deepcopy()

            # we assume that normalizer is not changing w.r.t xc!
            # so we avoid re-training the normalizer on dataset including xc

            if self.classifier.preprocess is not None:
                self._poisoned_clf.retrain_normalizer = train_normalizer

            self._poisoned_clf.fit(tr)

        return self._poisoned_clf, tr

    ###########################################################################
    #                  OBJECTIVE FUNCTION & GRAD COMPUTATION
    ###########################################################################

    def _objective_function(self, xc, acc=False):
        """
        Parameters
        ----------
        xc: poisoning point

        Returns
        -------
        f_obj: values of objective function (average hinge loss) at x
        """

        # index of poisoning point within xc.
        # This will be replaced by the input parameter xc
        if self._idx is None:
            idx = 0
        else:
            idx = self._idx

        xc = CArray(xc).atleast_2d()

        n_samples = xc.shape[0]
        if n_samples > 1:
            raise TypeError("xc is not a single sample!")

        self._xc[idx, :] = xc
        clf, tr = self._update_poisoned_clf()

        # targeted attacks
        y_ts = self._y_target if self._y_target is not None else self.val.Y

        y_pred, score = clf.predict(self.val.X, return_decision_function=True)

        # TODO: binary loss check
        if self._attacker_loss.class_type != 'softmax':
            score = CArray(score[:, 1].ravel())

        if acc is True:
            error = CArray(y_ts != y_pred).ravel()  # compute test error
        else:
            error = self._attacker_loss.loss(y_ts, score)
        obj = error.mean()

        return obj

    def _objective_function_gradient(self, xc, normalization=True):
        """
        Compute the loss derivative wrt the attack sample xc

        The derivative is decomposed as:

        dl / x = sum^n_c=1 ( dl / df_c * df_c / x )
        """

        xc = xc.atleast_2d()
        n_samples = xc.shape[0]

        if n_samples > 1:
            raise TypeError("x is not a single sample!")

        # index of poisoning point within xc.
        # This will be replaced by the input parameter xc
        if self._idx is None:
            idx = 0
        else:
            idx = self._idx

        self._xc[idx, :] = xc
        clf, tr = self._update_poisoned_clf()

        y_ts = self._y_target if self._y_target is not None else self.val.Y

        # computing gradient of loss(y, f(x)) w.r.t. f
        score = clf.predict(self.val.X, return_decision_function=True)[1]

        grad = CArray.zeros((xc.size,))

        if clf.n_classes <= 2:
            loss_grad = self._attacker_loss.dloss(
                y_ts, CArray(score[:, 1]).ravel())
            grad = self._gradient_fk_xc(
                self._xc[idx, :], self._yc[idx], clf, loss_grad, tr)
        else:
            # compute the gradient as a sum of the gradient for each class
            for c in range(clf.n_classes):
                loss_grad = self._attacker_loss.dloss(y_ts, score, c=c)

                grad += self._gradient_fk_xc(self._xc[idx, :], self._yc[idx],
                                             clf, loss_grad, tr, c)

        if normalization:
            norm = grad.norm()
            return grad / norm if norm > 0 else grad
        else:
            return grad

    @abstractmethod
    def _gradient_fk_xc(self, xc, yc, clf, loss_grad, tr, k=None):
        """
        Derivative of the classifier's discriminant function f_k(x)
        computed on a set of points x w.r.t. a single poisoning point xc

        This is a classifier-specific implementation, so we delegate its
        implementation to inherited classes.
        """
        pass

    ###########################################################################
    #         POISONING INTERNAL ROUTINE ON SINGLE DATA POINT (PRIVATE)
    ###########################################################################

    def _run(self, xc, yc, idx=0):
        """
        Single point poisoning
        Here xc can be a *set* of points, in which case idx specifies which
        point should be manipulated by the poisoning attack

        :param xc: init poisoning points
        :param yc: poisoning point labels
        :param val: validation dataset
        :param n_iter: maximum number of solver iterations
        :param idx: index of point in xc to be manipulated to poison clf
        :return:
        """
        xc = CArray(xc.deepcopy()).atleast_2d()

        self._yc = yc
        self._xc = xc
        self._idx = idx  # point to be optimized within xc

        self._x0 = self._xc[idx, :].ravel()

        self._init_solver()

        if self.y_target is None:  # indiscriminate attack
            x = self._solver.maximize(self._x0)
        else:  # targeted attack
            x = self._solver.minimize(self._x0)

        self._solution_from_solver()

        return x

    ###########################################################################
    #                              PUBLIC METHODS
    ###########################################################################

    def run(self, x, y, ds_init=None, max_iter=1):
        """Runs poisoning on multiple points.

        It reads n_points (previously set), initializes xc, yc at random,
        and then optimizes the poisoning points xc.

        Parameters
        ----------
        x : CArray
            Validation set for evaluating classifier performance.
            Note that this is not the validation data used by the attacker,
            which should be passed instead to `CAttackPoisoning` init.
        y : CArray
            Corresponding true labels for samples in `x`.
        ds_init : CDataset or None, optional.
            Dataset for warm start.
        max_iter : int, optional
            Number of iterations to re-optimize poisoning data. Default 1.

        Returns
        -------
        y_pred : predicted labels for all val samples by targeted classifier
        scores : scores for all val samples by targeted classifier
        adv_xc : manipulated poisoning points xc (for subsequents warm starts)
        f_opt : final value of the objective function

        """
        if self._n_points is None or self._n_points == 0:
            # evaluate performance on x,y
            y_pred, scores = self._classifier.predict(
                x, return_decision_function=True)
            return y_pred, scores, ds_init, 0

        # n_points > 0
        if self.init_type == 'random':
            # randomly sample xc and yc
            xc, yc = self._rnd_init_poisoning_points()
        elif self.init_type == 'loss_based':
            xc, yc = self._loss_based_init_poisoning_points()
        else:
            raise NotImplementedError(
                "Unknown poisoning point initialization strategy.")

        # re-set previously-optimized points if passed as input
        if ds_init is not None:
            xc[0:ds_init.num_samples, :] = ds_init.X
            yc[0:ds_init.num_samples] = ds_init.Y

        delta = 1.0
        k = 0

        # max_iter ignored for single-point attacks
        if self.n_points == 1:
            max_iter = 1

        metric = CMetric.create('accuracy')

        while delta > 0 and k < max_iter:

            self.logger.info(
                "Iter on all the poisoning samples: {:}".format(k))

            xc_prv = xc.deepcopy()
            for i in range(self._n_points):
                # this is to optimize the last points first
                # (and then re-optimize the first ones)
                idx = self.n_points - i - 1
                xc[idx, :] = self._run(xc, yc, idx=idx)
                # optimizing poisoning point 0
                self.logger.info(
                    "poisoning point {:} optim fopt: {:}".format(
                        i, self._f_opt))

                y_pred, scores = self._poisoned_clf.predict(
                    x, return_decision_function=True)
                acc = metric.performance_score(y_true=y, y_pred=y_pred)
                self.logger.info("Poisoned classifier accuracy "
                                 "on test data {:}".format(acc))

            delta = (xc_prv - xc).norm_2d()
            self.logger.info(
                "Optimization with n points: " + str(self._n_points) +
                " iter: " + str(k) + ", delta: " +
                str(delta) + ", fopt: " + str(self._f_opt))
            k += 1

        # re-train the targeted classifier (copied) on poisoned data
        # to evaluate attack effectiveness on targeted classifier
        clf, tr = self._update_poisoned_clf(clf=self._classifier,
                                            tr=self._training_data,
                                            train_normalizer=False)
        # fixme: rechange train_normalizer=True

        y_pred, scores = clf.predict(x, return_decision_function=True)
        acc = metric.performance_score(y_true=y, y_pred=y_pred)
        self.logger.info(
            "Original classifier accuracy on test data {:}".format(acc))

        return y_pred, scores, CDataset(xc, yc), self._f_opt

    def add_discrete_perturbation(self, xc):

        # FIXME: ETA WAS A SOLVER PARAM BUT NOW IS A C_ATTACK_POISONING PARAM
        eta = self.eta

        # for each poisoning point
        for p_idx in range(xc.shape[0]):

            c_xc = xc[p_idx, :]

            # for each starting poisoning point
            # add a perturbation large eta to a single feature of xc if the
            # perturbation if possible (if at least one feature perturbed
            # does not violate the constraints)
            orig_xc = c_xc.deepcopy()
            shuf_feat_ids = CArray.arange(c_xc.size)
            shuf_feat_ids.shuffle()

            for idx in shuf_feat_ids:

                # update a randomly chosen feature of xc if does not
                # violates any constraint
                c_xc[idx] += eta

                self._x0 = c_xc
                bounds, constr = self._constraint_creation()
                if bounds.is_violated(c_xc) or \
                        bounds.is_violated(c_xc):
                    c_xc = orig_xc.deepcopy()

                    c_xc[idx] -= eta

                    # update a randomly chosen feature of xc if does not
                    # violates any constraint
                    self._x0 = c_xc
                    bounds, constr = self._constraint_creation()
                    if bounds.is_violated(c_xc) or \
                            bounds.is_violated(c_xc):
                        c_xc = orig_xc.deepcopy()
                    else:
                        xc[p_idx, :] = c_xc
                        break
                else:
                    xc[p_idx, :] = c_xc
                    break

        return xc

    def _loss_based_init_poisoning_points(self, n_points=None):
        """
        """
        raise NotImplementedError

    def _compute_grad_inv(self, G, H, grad_loss_params):

        from scipy import linalg
        det = linalg.det(H.tondarray())
        if abs(det) < 1e-6:
            H_inv = CArray(linalg.pinv2(H.tondarray()))
        else:
            H_inv = CArray(linalg.inv(H.tondarray()))
        grad_mat = - CArray(G.dot(H_inv))  # d * (d + 1)

        self._d_params_xc = grad_mat

        gt = grad_mat.dot(grad_loss_params)
        return gt.ravel()

    def _compute_grad_solve(self, G, H, grad_loss_params, sym_pos=True):

        from scipy import linalg
        v = linalg.solve(
            H.tondarray(), grad_loss_params.tondarray(), sym_pos=sym_pos)
        v = CArray(v)
        gt = -G.dot(v)
        return gt.ravel()

    def _compute_grad_solve_iterative(self, G, H, grad_loss_params, tol=1e-6):
        from scipy.sparse import linalg

        if self._warm_start is None:
            v, convergence = linalg.cg(
                H.tondarray(), grad_loss_params.tondarray(), tol=tol)
        else:
            v, convergence = linalg.cg(
                H.tondarray(), grad_loss_params.tondarray(), tol=tol,
                x0=self._warm_start.tondarray())

        if convergence != 0:
            warnings.warn('Convergence of poisoning algorithm not reached!')

        v = CArray(v.ravel())

        # store v to be used as warm start at the next iteration
        self._warm_start = v

        gt = -G.dot(v.T)
        return gt.ravel()
