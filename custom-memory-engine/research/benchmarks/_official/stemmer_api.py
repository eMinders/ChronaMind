"""Exact NLTK 3.8.1 stemming interface; Apache-2.0, see NLTK_LICENSE.txt."""

from abc import ABCMeta, abstractmethod


class StemmerI(metaclass=ABCMeta):
    """
    A processing interface for removing morphological affixes from
    words.  This process is known as stemming.

    """

    @abstractmethod
    def stem(self, token):
        """
        Strip affixes from the token and return the stem.

        :param token: The token that should be stemmed.
        :type token: str
        """
