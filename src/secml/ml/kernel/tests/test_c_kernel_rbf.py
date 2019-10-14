from secml.ml.kernel.tests import CCKernelTestCases


class TestCKernelRBF(CCKernelTestCases):
    """Unit test for CKernelRBF."""

    def setUp(self):
        self._set_up('rbf')

    def test_similarity_shape(self):
        """Test shape of kernel."""
        self._test_similarity_shape()

    def test_gradient(self):
        self._test_gradient()
        self._test_gradient_sparse()


if __name__ == '__main__':
    CCKernelTestCases.main()
