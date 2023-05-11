"Table to store results"

import logging
import os
import shutil
from enum import Enum
from typing import Any

from openpyxl import load_workbook

from lib.addressfile import AddressFile
from lib.datatypes import MonthYear
from lib.detailsfile import AccountDetailsFileSingleton, GvsDetailsRecord
from lib.exceptions import NoServiceRow
from lib.helpers import BaseWorkBook, ExcelHelpers
from lib.osvfile import OsvAccuralRecord, OsvAddressRecord
from lib.reaccural import ReaccuralType
from lib.tariffs import HeatingTariff

GvsIpuInstallDates: dict[str, str] = {}


class ResultRecordType(Enum):
    "Types of rows in result file"
    HEATING_ACCURAL = 1
    HEATING_REACCURAL = 2
    GVS_ACCURAL = 3
    GVS_REACCURAL = 4


class BaseResultRow:
    "Base class for a row of result table"
    MAX_FIELDS = 47

    def set_field(self, ind: int, value: str | None = None):
        "Field setter by field number"
        setattr(self, f"f{ind:02d}", value)

    def get_field(self, ind: int) -> str | None:
        "Field getter by field number"
        return getattr(self, f"f{ind:02d}")

    def __init__(
        self,
        date: MonthYear,
        data: OsvAddressRecord,
    ) -> None:
        for ind in range(self.MAX_FIELDS):
            setattr(self, f"f{ind:02d}", None)
        self.set_field(0, date.month)
        self.set_field(1, date.year)
        self.set_field(2, data.account)
        self.set_field(3, data.address)
        self.set_field(6, date.month)
        self.set_field(7, date.year)
        self.price = HeatingTariff.get_tariff(date)
        self.set_field(8, self.price)

    def as_list(self) -> list[Any]:
        "Returns list of all fields"
        result = []
        for ind in range(self.MAX_FIELDS):
            result.append(getattr(self, f"f{ind:02d}"))
        return result


class HeatingResultRow(BaseResultRow):
    "Result row for Heating service"

    def __init__(
        self,
        date: MonthYear,
        data: OsvAddressRecord,
        accural: OsvAccuralRecord,
        odpu_file: AddressFile,
        heating_average_file: AddressFile,
        account_details: AccountDetailsFileSingleton,
    ) -> None:
        super().__init__(date, data)
        self.set_field(4, ResultRecordType.HEATING_ACCURAL.name)
        self.set_field(5, "Отопление")
        # chapter 2:
        odpus = odpu_file.get_sheet_data_formatted(str(date.year))
        has_odpu: bool = ExcelHelpers.address_in_list(data.address, odpus)
        if has_odpu:
            self.set_field(9, "Общедомовый")
            self.set_field(10, "01.01.2018")
            self.set_field(11, "Подвал")
            self.set_field(12, 1)
            self.set_field(13, "ВКТ-5")
            self.set_field(14, 1)
            self.set_field(15, 6)
            self.set_field(16, 3)
        # chapter 3:
        heating_averages = heating_average_file.get_sheet_data_formatted(str(date.year))
        # 'data_formatted' is used here instead of 'data_raw', because
        # value of heating average is not required in result table,
        # we only need address of the building to know that heating average was calculated
        has_heating_average: bool = ExcelHelpers.address_in_list(
            data.address, heating_averages
        )
        # price = HeatingTariff.get_tariff(date)
        if has_odpu and has_heating_average:
            quantity = quantity_average = f"{float(accural.heating) / self.price:.4f}"
            sum_average = accural.heating
            self.set_field(26, quantity_average)
            self.set_field(27, sum_average)
            self.set_field(28, sum_average)
        else:
            # chapter 4:
            self.set_field(30, data.population)
            quantity = quantity_normative = f"{accural.heating / self.price:.4f}"
            sum_normative = accural.heating
            self.set_field(31, quantity_normative)
            self.set_field(32, sum_normative)
            self.set_field(33, sum_normative)
        # chapter 5:
        self.set_field(35, quantity)
        self.set_field(36, accural.heating)
        self.set_field(37, accural.heating)
        # chapter 6:
        payment_sum = account_details.get_service_month_payment(date, "Отопление")
        if payment_sum != 0:
            self.set_field(40, f"20.{date.month:02d}.{date.year}")
            self.set_field(41, f"20.{date.month:02d}.{date.year}")
            self.set_field(42, payment_sum)
            self.set_field(43, "Оплата" if payment_sum else "Возврат оплаты")
        # chapter 7:
        self.set_field(
            45, account_details.get_service_month_closing_balance(date, "Отопление")
        )


class GvsSingleResultRow(BaseResultRow):
    "Result row for GVS service for cases where there is only one GVS details record"

    @staticmethod
    def _get_new_counter_number(seed: str):
        return f"{seed}_2"

    def __init__(
        self,
        date: MonthYear,
        data: OsvAddressRecord,
        accural: OsvAccuralRecord,
        account_details: AccountDetailsFileSingleton,
        gvs_details_row: GvsDetailsRecord,
    ) -> None:
        super().__init__(date, data)
        self.set_field(4, ResultRecordType.GVS_ACCURAL.name)
        self.set_field(5, "Тепловая энергия ГВС")
        # chapter 3:
        gvs = gvs_details_row
        if gvs.counter_id or gvs.counter_number:
            self.set_field(9, "Индивидуальный")
            self.set_field(10, GvsIpuInstallDates.get(data.account, "01.01.2019"))
            self.set_field(13, "СГВ-15")
            if not gvs.counter_number:
                gvs.counter_number = self._get_new_counter_number(gvs.counter_id)
            self.set_field(14, gvs.counter_number)
            self.set_field(15, 6)
            self.set_field(16, 3)
            # chapter 4:
            if gvs.metric_current is not None:
                self.set_field(19, gvs.metric_date_current)
                self.set_field(20, "От абонента (прочие)")
                self.set_field(21, gvs.metric_current)
            self.set_field(22, gvs.consumption_ipu)
        # chapter 5:
        quantity = f"{accural.gvs/self.price:.4f}"
        if gvs.consumption_ipu:
            self.set_field(23, quantity)
            self.set_field(24, accural.gvs)
            self.set_field(25, accural.gvs)
        # chapter 6:
        if gvs.consumption_average:
            self.set_field(26, quantity)
            self.set_field(27, accural.gvs)
            self.set_field(28, accural.gvs)
        # chapter 7:
        if gvs.consumption_normative:
            self.set_field(30, gvs.people_registered)
            self.set_field(31, quantity)
            self.set_field(32, accural.gvs)
            self.set_field(33, accural.gvs)
        # chapter 8:
        self.set_field(35, quantity)
        self.set_field(36, accural.gvs)
        self.set_field(37, accural.gvs)
        # chapter 9:
        try:
            payment_sum = account_details.get_service_month_payment(
                date, "Тепловая энергия для подогрева воды"
            )
        except NoServiceRow:
            payment_sum = 0
        if payment_sum != 0:
            self.set_field(40, f"20.{date.month:02d}.{date.year}")
            self.set_field(41, f"20.{date.month:02d}.{date.year}")
            self.set_field(42, payment_sum)
            self.set_field(43, "Оплата" if payment_sum else "Возврат оплаты")
        # chapter 10:
        try:
            closing_balance = account_details.get_service_month_closing_balance(
                date, "Тепловая энергия для подогрева воды"
            )
        except NoServiceRow:
            closing_balance = 0
        self.set_field(45, closing_balance)


class GvsMultipleResultFirstRow(GvsSingleResultRow):
    """
    Result row for GVS service for cases where there are two GVS details records.
    The first of two such rows
    """

    def __init__(
        self,
        date: MonthYear,
        data: OsvAddressRecord,
        accural: OsvAccuralRecord,
        account_details: AccountDetailsFileSingleton,
        gvs_details_row: GvsDetailsRecord,
    ) -> None:
        super().__init__(date, data, accural, account_details, gvs_details_row)
        gvs = gvs_details_row
        if gvs.metric_current is not None:
            self.set_field(20, "При снятии прибора")


class GvsMultipleResultSecondRow(GvsSingleResultRow):
    """
    Result row for GVS service for cases where there are two GVS details records.
    The second of two such rows
    """

    def __init__(
        self,
        date: MonthYear,
        data: OsvAddressRecord,
        accural: OsvAccuralRecord,
        account_details: AccountDetailsFileSingleton,
        gvs_details_row: GvsDetailsRecord,
    ) -> None:
        super().__init__(date, data, accural, account_details, gvs_details_row)
        gvs = gvs_details_row
        self.set_field(10, gvs.metric_date_current)
        GvsIpuInstallDates[gvs.account] = gvs.metric_date_current
        if gvs.metric_current is not None:
            self.set_field(20, "При установке")
        for i in range(23, 46):
            self.set_field(i, None)


class GvsReaccuralResultRow(BaseResultRow):
    "Result row for GVS reaccural"

    def __init__(
        self,
        date: MonthYear,
        data: OsvAddressRecord,
        gvs_details_row: GvsDetailsRecord,
        reaccural_date: MonthYear,
        reaccural_sum: float,
        reaccural_type: ReaccuralType,
    ) -> None:
        super().__init__(date, data)
        self.set_field(4, ResultRecordType.GVS_REACCURAL.name)
        self.set_field(5, "Тепловая энергия ГВС")
        # chapter 2:
        self.set_field(6, reaccural_date.month)
        self.set_field(7, reaccural_date.year)
        self.set_field(8, self.price)
        # chapter 3: same as GvsSingleResultRow
        gvs = gvs_details_row
        if gvs.counter_id or gvs.counter_number:
            self.set_field(9, "Индивидуальный")
            self.set_field(10, GvsIpuInstallDates.get(data.account, "01.01.2019"))
            self.set_field(13, "СГВ-15")
            if not gvs.counter_number:
                gvs.counter_number = GvsSingleResultRow._get_new_counter_number(
                    gvs.counter_id
                )
            self.set_field(14, gvs.counter_number)
            self.set_field(15, 6)
            self.set_field(16, 3)
        quantity = f"{reaccural_sum/self.price:.4f}"
        # chapter 5: same as chapter 7 of GvsSingleResultRow
        match reaccural_type:
            case ReaccuralType.IPU:
                self.set_field(23, quantity)
                self.set_field(24, reaccural_sum)
                self.set_field(25, reaccural_sum)
            case ReaccuralType.AVERAGE:
                self.set_field(26, quantity)
                self.set_field(27, reaccural_sum)
                self.set_field(28, reaccural_sum)
            case ReaccuralType.NORMATIVE:
                self.set_field(31, quantity)
                self.set_field(32, reaccural_sum)
                self.set_field(33, reaccural_sum)
            case _:
                raise ValueError
        # chapter 6: same as chapter 8 of GvsSingleResultRow
        self.set_field(35, quantity)
        self.set_field(36, reaccural_sum)
        self.set_field(37, reaccural_sum)


class ResultFile(BaseWorkBook):
    """Table of results"""

    def __init__(self, base_dir: str, conf: dict) -> None:
        self.base_dir = base_dir
        self.conf = conf
        file_name, self.sheet_name = conf["result_file"].split("@", 2)
        self.file_name_full = os.path.join(self.base_dir, file_name)
        template_name_full = os.path.join(
            os.path.dirname(self.base_dir), conf["result_template"]
        )
        logging.info("Initialazing result table %s ...", self.file_name_full)
        shutil.copyfile(template_name_full, self.file_name_full)
        self.workbook = load_workbook(filename=self.file_name_full)
        self.sheet = self.workbook[self.sheet_name]
        logging.info("Initialazing result table done")

    def save(self) -> None:
        """Saves result table data to disk"""
        logging.info("Saving results table...")
        self.workbook.save(filename=self.file_name_full)
        logging.info("Saving results table done")

    def add_row(self, row: BaseResultRow):
        "Adds row to table"
        self.sheet.append(row.as_list())