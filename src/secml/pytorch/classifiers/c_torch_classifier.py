"""
.. module:: CTorchClassifier
   :synopsis: Classifier with PyTorch Neural Network

.. moduleauthor:: Ambra Demontis <marco.melis@diee.unica.it>
.. moduleauthor:: Marco Melis <marco.melis@diee.unica.it>

"""
from copy import deepcopy
from abc import ABCMeta, abstractproperty, abstractmethod

import torch
from torch.utils.data import DataLoader
import torch.optim as optim
from torch.autograd import Variable

from secml.array import CArray
from secml.data import CDataset
from secml.ml.classifiers import CClassifier

from secml.core.settings import SECML_PYTORCH_USE_CUDA
from secml.pytorch.data import CTorchDataset
from secml.pytorch.utils import AverageMeter, accuracy
from secml.pytorch.utils.optim_utils import add_weight_decay

# Use CUDA ?!
use_cuda = torch.cuda.is_available() and SECML_PYTORCH_USE_CUDA

torch.manual_seed(999)
if use_cuda:
    torch.cuda.manual_seed_all(999)


# FIXME: inner preprocess not manage yet for training phase
class CTorchClassifier(CClassifier):
    """PyTorch Neural Network classifier.

    Parameters
    ----------
    batch_size : int
        Size of the batch for grouping samples. Depends on the
        neural network model and on the specific data.
    learning_rate : float, optional
        Learning rate. Default 1e-2.
    momentum : float, optional
        Momentum factor. Default 0.9.
    weight_decay : float, optional
        Weight decay (L2 penalty). Control parameters regularization.
        Default 1e-4.
    epochs : int, optional
        Number of epochs. Default 100.
    gamma : float, optional
        Multiplicative factor of learning rate decay. Default: 0.1.
    lr_schedule : list, optional
        List of epoch indices. Must be increasing.
        The current learning rate will be multiplied by gamma
        once the number of epochs reaches each index.
    regularize_bias : bool, optional
        If False, L2 regularization will NOT be applied to biases.
        Default True, so regularization will be applied to all parameters.
        If weight_decay is 0, regularization will not be applied anyway.
        If fit.warm_start is True, this parameter has no effect.
    train_transform : torchvision.transform or None, optional
        Transformation to be applied before training.
    preprocess : CNormalizer or None, optional
        Preprocessing for data.

    """
    __metaclass__ = ABCMeta
    __super__ = 'CTorchClassifier'

    def __init__(self, batch_size, learning_rate=1e-2, momentum=0.9,
                 weight_decay=1e-4, epochs=100, gamma=0.1,
                 lr_schedule=(50, 75), regularize_bias=True,
                 train_transform=None, preprocess=None):

        # Model params
        self._batch_size = batch_size

        # Optimizer params
        self._learning_rate = learning_rate
        self._momentum = momentum
        self._weight_decay = float(weight_decay)

        # Training params
        self._epochs = epochs
        self._gamma = gamma
        self._lr_schedule = lr_schedule
        self._regularize_bias = regularize_bias
        self._train_transform = train_transform

        self._init_params = {'batch_size': batch_size,
                             'learning_rate': learning_rate,
                             'momentum': momentum,
                             'weight_decay': weight_decay,
                             'epochs': epochs,
                             'gamma': gamma,
                             'lr_schedule': lr_schedule,
                             'regularize_bias': regularize_bias,
                             'train_transform': train_transform}

        # Training vars
        self._start_epoch = 0

        # PyTorch NeuralNetwork model
        self._model = None
        # PyTorch Optimizer
        self._optimizer = None

        # Initialize the model (implementation specific for each clf)
        self.init_model()
        # Initialize the optimizer
        self.init_optimizer()

        if use_cuda is True:
            self.logger.info("Using CUDA for PyTorch computations!")

        super(CTorchClassifier, self).__init__(preprocess=preprocess)

    @property
    def learning_rate(self):
        """Learning rate of the optimizer."""
        return self._learning_rate

    @learning_rate.setter
    def learning_rate(self, value):
        """Learning rate of the optimizer."""
        self._learning_rate = float(value)
        # We need to recreate the optimizer after param change
        self.init_optimizer()

    @property
    def momentum(self):
        """Momentum of the optimizer."""
        return self._momentum

    @momentum.setter
    def momentum(self, value):
        """Momentum of the optimizer."""
        self._momentum = float(value)
        # We need to recreate the optimizer after param change
        self.init_optimizer()

    @property
    def weight_decay(self):
        """L2 penalty of the optimizer."""
        return self._weight_decay

    @weight_decay.setter
    def weight_decay(self, value):
        """L2 penalty of the optimizer."""
        self._weight_decay = float(value)
        # We need to recreate the optimizer after param change
        self.init_optimizer()

    @property
    def w(self):
        w = CArray([])
        with torch.no_grad():
            for m in self._model.modules():
                if hasattr(m, 'weight') and m.weight is not None:
                    w = w.append(CArray(m.weight.data.cpu().numpy()), axis=None)
        return w

    @property
    def b(self):
        b = CArray([])
        with torch.no_grad():
            for m in self._model.modules():
                if hasattr(m, 'bias') and m.bias is not None:
                    b = b.append(CArray(m.bias.data.cpu().numpy()), axis=None)
        return b

    @w.setter
    def w(self, val):
        """
        :param val: flat CArray
        :return:
        """
        with torch.no_grad():
            starting_w = 0
            for m in self._model.modules():
                if hasattr(m, 'weight') and m.weight is not None:
                    lyr_size = m.weight.data.cpu().numpy().size
                    lyr_shape = m.weight.data.cpu().numpy().shape
                    lyr_w = val[starting_w:(starting_w + lyr_size)].reshape(lyr_shape).tondarray()
                    lyr_w = torch.from_numpy(lyr_w)
                    lyr_w = lyr_w.type(torch.FloatTensor)
                    if len(lyr_shape) > 1:
                        m.weight[:, :] = lyr_w[:, :]
                    else:
                        m.weight[:] = lyr_w[:]
                    starting_w += lyr_size

    @b.setter
    def b(self, val):
        """
        :param val: flat CArray
        :return:
        """
        with torch.no_grad():
            starting_b = 0
            for m in self._model.modules():
                if hasattr(m, 'bias') and m.bias is not None:
                    lyr_size = m.bias.data.cpu().numpy().size
                    lyr_shape = m.bias.data.cpu().numpy().shape
                    lyr_b = val[starting_b:(starting_b + lyr_size)].reshape(lyr_shape).tondarray()
                    lyr_b = torch.from_numpy(lyr_b)
                    lyr_b = lyr_b.type(torch.FloatTensor)
                    if len(lyr_shape) > 1:
                        m.bias[:, :] = lyr_b[:, :]
                    else:
                        m.bias[:] = lyr_b[:]
                    starting_b += lyr_size

    def __deepcopy__(self, memo, *args, **kwargs):
        """Called when copy.deepcopy(object) is called.

        `memo` is a memory dictionary needed by `copy.deepcopy`.

        """
        # Store and deepcopy the state of the optimizer/model
        state_dict = deepcopy(self.state_dict())

        # Remove optimizer and model before deepcopy (will be restored)
        optimizer = self._optimizer
        model = self._model
        self._optimizer = None
        self._model = None

        # Now we are ready to clone the clf
        new_obj = super(
            CTorchClassifier, self).__deepcopy__(memo, *args, **kwargs)

        # Restore optimizer/model in the current object
        self._optimizer = optimizer
        self._model = model

        # Set optimizer/model state in new object
        new_obj.init_model()
        new_obj.init_optimizer()
        new_obj.load_state(state_dict)

        return new_obj

    def init_model(self):
        """Initialize the PyTorch Neural Network model."""
        # Call the specific model initialization method
        self._init_model()
        # Ensure we are using cuda if available
        if use_cuda is True:
            self._model = self._model.cuda()

    @abstractmethod
    def _init_model(self):
        """Initialize the PyTorch Neural Network model."""
        raise NotImplementedError

    def init_optimizer(self):
        """Initialize the PyTorch Neural Network optimizer."""
        # Altering parameters by adding weight_decay only to proper params
        if self.weight_decay != 0 and self._regularize_bias is False:
            params = add_weight_decay(self._model, self.weight_decay)
        else:  # .. but only if necessary!
            params = self._model.parameters()

        # weight_decay is passed anyway to the optimizer and act as a default
        self._optimizer = optim.SGD(params,
                                    lr=self._learning_rate,
                                    momentum=self._momentum,
                                    weight_decay=self.weight_decay)

    @abstractmethod
    def loss(self, x, target):
        """Return the loss function computed on input."""
        raise NotImplementedError

    def _to_tensor(self, x):
        """Convert input array to tensor."""
        x = x.atleast_2d()
        x = x.tondarray()
        x = torch.from_numpy(x)
        x = x.type(torch.FloatTensor)
        return x

    def _get_test_input_loader(self, x, n_jobs=1):
        """Return a loader for input test data."""
        # Convert to CTorchDataset and use a dataloader that returns batches
        return DataLoader(CTorchDataset(x),
                          batch_size=self._batch_size,
                          shuffle=False,
                          num_workers=n_jobs-1)

    def load_state(self, state_dict, dataparallel=False):
        """Load PyTorch objects state from dictionary.

        Parameters
        ----------
        state_dict : dict
            Dictionary with the state of the model, optimizer and last epoch.
            Should contain the following keys:
                - 'state_dict' state of the model as by model.state_dict()
                - 'optimizer' state of the optimizer as by optimizer.state_dict()
                - 'epoch' last epoch of the training process
        dataparallel : bool, optional
            If True, input state should be considered saved from a
            DataParallel model. Default False.

        """
        # Set this to True if optimizer needs to be recreated
        recreate_optimizer = False
        # Change optimizer-related parameters accordingly to state
        # The default (initial) parameters are stored
        # Parameters in `param_groups` list could be different
        # depending on the epoch the state has been stored
        # and will be restored later
        if 'defaults' in state_dict['optimizer']:
            defaults = state_dict['optimizer']['defaults']
            self._learning_rate = defaults['lr']
            self._momentum = defaults['momentum']
            self._weight_decay = defaults['weight_decay']
            recreate_optimizer = True
        else:
            # If the state dict does not contain the default values,
            # display warning and continue
            self.logger.warning("State dictionary has no defaults for the "
                                "optimizer parameters. Keeping current values")

        try:  # biases have been regularized?
            self._regularize_bias = bool(
                state_dict['optimizer']['regularize_bias'])
            recreate_optimizer = True
        except KeyError:
            pass  # `regularize_bias` not defined probably, use default

        if recreate_optimizer is True:
            self.init_optimizer()

        # Restore the state of the param_groups in the optimizer
        self._optimizer.load_state_dict(state_dict['optimizer'])

        # Restore the count of epochs
        self._start_epoch = state_dict['epoch'] + 1

        # Restore the state of the model
        if dataparallel is True:
            # Convert a DataParallel model state to a normal model state
            # Get the keys to alter the dict on-the-fly
            keys = state_dict['state_dict'].keys()
            for k in keys:
                name = k.replace('module.', '')  # remove module.
                state_dict['state_dict'][name] = state_dict['state_dict'][k]
                state_dict['state_dict'].pop(k)
        self._model.load_state_dict(state_dict['state_dict'])

    def state_dict(self):
        """Return a dictionary with PyTorch objects state.

        Returns
        ----------
        dict
            Dictionary with the state of the model, optimizer and last epoch.
            Will contain the following keys:
                - 'state_dict' state of the model as by model.state_dict()
                - 'optimizer' state of the optimizer as by optimizer.state_dict()
                - 'epoch' last epoch of the training process

        """
        state_dict = dict()
        state_dict['optimizer'] = self._optimizer.state_dict()
        # Saving other optimizer default parameters
        state_dict['optimizer']['defaults'] = self._optimizer.defaults
        state_dict['optimizer']['regularize_bias'] = self._regularize_bias
        state_dict['state_dict'] = self._model.state_dict()
        state_dict['epoch'] = self._start_epoch
        return state_dict

    def fit(self, dataset, warm_start=False, n_jobs=1):
        """Trains the classifier.

        If a preprocess has been specified,
        input is normalized before training.

        For multiclass case see `.CClassifierMulticlass`.

        Parameters
        ----------
        dataset : CDataset
            Training set. Must be a :class:`.CDataset` instance with
            patterns data and corresponding labels.
        warm_start : bool, optional
            If False (default) model will be reinitialized before training.
            Otherwise the state of the model will be preserved.
        n_jobs : int, optional
            Number of parallel workers to use for training the classifier.
            Default 1. Cannot be higher than processor's number of cores.

        Returns
        -------
        trained_cls : CClassifier
            Instance of the classifier trained using input dataset.

        """
        if not isinstance(dataset, CDataset):
            raise TypeError(
                "training set should be provided as a CDataset object.")

        if self.preprocess is not None:
            self.logger.warning(
                "preprocess is not applied to training data. "
                "Use `train_transform` parameter if necessary.")

        if warm_start is False:
            # Resetting the classifier
            self.clear()
            # Storing dataset classes
            self._classes = dataset.classes
            self._n_features = dataset.num_features
            # Reinitialize the model as we are starting clean
            self.init_model()
            # Reinitialize count of epochs
            self._start_epoch = 0
            # Reinitialize the optimizer as we are starting clean
            self.init_optimizer()

        return self._fit(dataset, n_jobs=n_jobs)

    def _fit(self, dataset, n_jobs=1):
        """At each training the weight are setted equal to the random weight
        that are chosen when we are instantiating the object

        :param trX:
        :param trY:
        :return:

        """
        # Binarize labels using a OVA scheme
        ova_labels = dataset.get_labels_asbinary()

        # Convert to CTorchDataset and use a dataloader that returns batches
        ds_loader = DataLoader(CTorchDataset(dataset.X, ova_labels,
                                             transform=self._train_transform),
                               batch_size=self._batch_size,
                               shuffle=True,
                               num_workers=n_jobs-1)

        # Switch to training mode
        self._model.fit()

        # Scheduler to adjust the learning rate depending on epoch
        scheduler = optim.lr_scheduler.MultiStepLR(
            self._optimizer, list(self._lr_schedule), gamma=self._gamma,
            last_epoch=self._start_epoch - 1)

        for e_idx in xrange(self._start_epoch, self._epochs):

            scheduler.step()  # Adjust the learning rate
            losses = AverageMeter()  # Logger of the loss value
            acc = AverageMeter()  # Logger of the accuracy

            for batch_idx, (x, y) in enumerate(ds_loader):

                if use_cuda is True:
                    x, y = x.cuda(), y.cuda(async=True)
                x, y = Variable(x, requires_grad=True), Variable(y)

                # compute output and loss
                logits = self._model(x)
                loss = self.loss(logits, y)

                # compute gradient and do SGD step
                self._optimizer.zero_grad()  # same as self._model.zero_grad()
                loss.backward()
                self._optimizer.step()

                losses.update(loss.item(), x.size(0))
                acc.update(accuracy(logits.data, y.data)[0], x.size(0))

                # Log progress
                self.logger.info('EPOCH {epoch} ({batch}/{size}) '
                                 'Loss: {loss:.4f} Acc: {acc:.2f}'.format(
                                    epoch=e_idx,
                                    batch=batch_idx + 1,
                                    size=len(ds_loader),
                                    loss=losses.avg,
                                    acc=acc.avg,
                                 ))

            self._start_epoch = e_idx

        return self

    def decision_function(self, x, y, n_jobs=1):
        """Computes the decision function for each pattern in x.

        If a preprocess has been specified, input is normalized
        before computing the decision function.

        Parameters
        ----------
        x : CArray
            Array with new patterns to classify, 2-Dimensional of shape
            (n_patterns, n_features).
        y : int
            The label of the class wrt the function should be calculated.
        n_jobs : int
            Number of parallel workers to use. Default 1.
            Cannot be higher than processor's number of cores.

        Returns
        -------
        score : CArray
            Value of the decision function for each test pattern.
            Dense flat array of shape (n_patterns,).

        """
        x = x.atleast_2d()  # Ensuring input is 2-D

        # Preprocessing data if a preprocess is defined
        if self.preprocess is not None:
            x = self.preprocess.normalize(x)

        return self._decision_function(x, y, n_jobs=n_jobs)

    def _decision_function(self, x, y, n_jobs=1):
        """Computes the decision function for each pattern in x.

        Parameters
        ----------
        x : CArray
            Array with new patterns to classify, 2-Dimensional of shape
            (n_patterns, n_features).
        y : int
            The label of the class wrt the function should be calculated.
        n_jobs : int
            Number of parallel workers to use. Default 1.
            Cannot be higher than processor's number of cores.

        Returns
        -------
        score : CArray
            Value of the decision function for each test pattern.
            Dense flat array of shape (n_patterns,).

        """
        x = x.atleast_2d()  # Ensuring input is 2-D

        x_loader = self._get_test_input_loader(x, n_jobs=n_jobs)

        # Switch to evaluation mode
        self._model.eval()

        scores = None
        for batch_idx, (s, _) in enumerate(x_loader):

            # Log progress
            self.logger.info(
                'Classification: {batch}/{size}'.format(
                    batch=batch_idx,
                    size=len(x_loader)
                ))

            if use_cuda is True:
                s = s.cuda()
            s = Variable(s, requires_grad=True)

            with torch.no_grad():
                logits = self._model(s)
                logits = logits.view(logits.size(0), -1)
                logits = CArray(
                    logits.data.cpu().numpy()[:, y]).astype(float)

            if scores is not None:
                scores = scores.append(logits, axis=0)
            else:
                scores = logits

        return scores.ravel()

    def predict(self, x, return_decision_function=False, n_jobs=1):
        """Perform classification of each pattern in x.

        If a preprocess has been specified,
         input is normalized before classification.

        Parameters
        ----------
        x : CArray
            Array with new patterns to classify, 2-Dimensional of shape
            (n_patterns, n_features).
        return_decision_function : bool, optional
            Whether to return the decision_function value along
            with predictions. Default False.
        n_jobs : int, optional
            Number of parallel workers to use for classification.
            Default 1. Cannot be higher than processor's number of cores.

        Returns
        -------
        labels : CArray
            Flat dense array of shape (n_patterns,) with the label assigned
             to each test pattern. The classification label is the label of
             the class associated with the highest score.
        scores : CArray, optional
            Array of shape (n_patterns, n_classes) with classification
             score of each test pattern with respect to each training class.
            Will be returned only if `return_decision_function` is True.

        """
        x_carray = CArray(x).atleast_2d()

        # Preprocessing data if a preprocess is defined
        if self.preprocess is not None:
            x_carray = self.preprocess.normalize(x_carray)

        x_loader = self._get_test_input_loader(x_carray, n_jobs=n_jobs)

        # Switch to evaluation mode
        self._model.eval()

        scores = None
        for batch_idx, (s, _) in enumerate(x_loader):

            # Log progress
            self.logger.info(
                'Classification: {batch}/{size}'.format(
                    batch=batch_idx,
                    size=len(x_loader)
                ))

            if use_cuda is True:
                s = s.cuda()
            s = Variable(s, requires_grad=True)

            with torch.no_grad():
                logits = self._model(s)
                logits = logits.view(logits.size(0), -1)
                logits = CArray(logits.data.cpu().numpy()).astype(float)

            if scores is not None:
                scores = scores.append(logits, axis=0)
            else:
                scores = logits

        # TODO: WE SHOULD USE SOFTMAX TO COMPUTE LABELS?
        # The classification label is the label of the class
        # associated with the highest score
        labels = scores.argmax(axis=1).ravel()

        return (labels, scores) if return_decision_function is True else labels

    def _gradient_f(self, x, y):
        """Computes the gradient of the classifier's decision function
         wrt decision function input.

        Parameters
        ----------
        x : CArray
            The gradient is computed in the neighborhood of x.
        y : int
            Index of the class wrt the gradient must be computed.

        Returns
        -------
        gradient : CArray
            Gradient of the classifier's df wrt its input. Vector-like array.

        """
        if x.is_vector_like is False:
            raise ValueError("gradient can be computed on one sample only.")

        dl = self._get_test_input_loader(x)

        s = dl.dataset[0][0]  # Get the single and only point from the dl

        if use_cuda is True:
            s = s.cuda()
        s = s.unsqueeze(0)  # Get a [1,h,w,c] tensor as required by the net
        s = Variable(s, requires_grad=True)

        # Switch to evaluation mode
        self._model.eval()

        logits = self._model(s)

        mask = torch.FloatTensor(s.shape[0], logits.shape[-1])
        mask.zero_()
        mask[0, y] = 1  # grad wrt first class neuron out
        if use_cuda is True:
            mask = mask.cuda()
        logits.backward(mask)

        return CArray(s.grad.data.cpu().numpy().ravel())

    def get_layer_output(self, x, layer=None):
        """Returns the output of the desired net layer.

        Parameters
        ----------
        x : CArray
            Input data.
        layer : str or None, optional
            Name of the layer.
            If None, the output of the last layer will be returned.

        Returns
        -------
        CArray
            Output of the desired layer.

        """
        x_loader = self._get_test_input_loader(x)

        # Switch to evaluation mode
        self._model.eval()

        out = None
        for batch_idx, (s, _) in enumerate(x_loader):

            if use_cuda is True:
                s = s.cuda()
            s = Variable(s, requires_grad=True)

            with torch.no_grad():
                # Manual iterate the network and stop at desired layer
                # Use _model to iterate over first level modules only
                for m_k, m in self._model._modules.iteritems():
                    s = m(s)  # Forward input trough module
                    if m_k == layer:
                        # We found the desired layer
                        break
                else:
                    if layer is not None:
                        raise ValueError("No layer `{:}` found!".format(layer))

            # Convert to CArray
            s = CArray(s.data.cpu().numpy())

            if out is not None:
                out = out.append(s, axis=0)
            else:
                out = s

        return out
