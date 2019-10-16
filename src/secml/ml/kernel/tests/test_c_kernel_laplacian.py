from secml.ml.kernel.tests import CCKernelTestCases


class TestCKernelLaplacian(CCKernelTestCases):
    """Unit test for CKernelLaplacian."""

    def setUp(self):
        self._set_up('laplacian')

    def test_similarity_shape(self):
        """Test shape of kernel."""
        self._test_similarity_shape()
        self._test_similarity_shape_sparse()

    def test_gradient(self):
        self._test_gradient()
        self._test_gradient_sparse()
        self._test_gradient_multiple_points()


if __name__ == '__main__':
    CCKernelTestCases.main()
