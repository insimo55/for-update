# backend/api/serializers.py
from rest_framework import serializers
from .models import User, Facility, Chemical, Inventory, Transaction, Requisition, RequisitionItem,  WellClosure, WellClosureItem, RequisitionItemDistribution, BudgetLine, Project
from django.db import transaction

class FacilitySerializer(serializers.ModelSerializer):
    class Meta:
        model = Facility
        fields = ('id', 'name', 'type', 'location', 'created_at')

class ChemicalSerializer(serializers.ModelSerializer):
    class Meta:
        model = Chemical
        fields = ('id', 'name', 'unit_of_measurement', 'description', 'price')
class UserSerializer(serializers.ModelSerializer):
    related_facility_name = serializers.CharField(source='related_facility.name', read_only=True, allow_null=True)
    password = serializers.CharField(write_only=True, required=False, allow_blank=True)
    class Meta:
        model = User
        fields = ('id', 'username', 'first_name', 'last_name', 'email', 'role', 'related_facility','password','related_facility_name')
        # extra_kwards = {'password': {'write_only': True}}
        read_only_fields = ('related_facility_name',)

    def create(self, validated_data):
        password = validated_data.pop('password', None)
        if password is None:
            raise serializers.ValidationError("Пароль является обязательным полем при создании пользователя.")
            
        # Создаем пользователя через менеджер, который правильно хеширует пароль
        user = User.objects.create_user(**validated_data)
        user.set_password(password)
        user.save()
        return user

    def update(self, instance, validated_data):
        password = validated_data.pop('password', None)
        # Если пароль был передан, обновляем его
        if password:
            instance.set_password(password)
        
        # Обновляем остальные поля
        return super().update(instance, validated_data)
    
    
# Сериализатор для отображения остатков с вложенной информацией
class InventorySerializer(serializers.ModelSerializer):
    # Показываем не просто id, а полную информацию об объекте и реагенте
    facility = FacilitySerializer(read_only=True)
    chemical = ChemicalSerializer(read_only=True)

    class Meta:
        model = Inventory
        fields = ('id', 'facility', 'chemical', 'quantity')

# Сериализатор для отображения транзакций
class TransactionSerializer(serializers.ModelSerializer):
    # Показываем имена, а не ID
    chemical = ChemicalSerializer(read_only=True) 
    from_facility = serializers.StringRelatedField(read_only=True)
    to_facility = serializers.StringRelatedField(read_only=True)
    performed_by = serializers.StringRelatedField(read_only=True)
    
    from_facility_id = serializers.PrimaryKeyRelatedField(
        source='from_facility', read_only=True, allow_null=True
    )
    to_facility_id = serializers.PrimaryKeyRelatedField(
        source='to_facility', read_only=True, allow_null=True
    )
    is_from_report = serializers.SerializerMethodField()
    class Meta:
        model = Transaction
        fields = (
            'id', 'timestamp', 'transaction_type', 'chemical', 'quantity',
            'from_facility', 'to_facility','from_facility_id', 'to_facility_id', 'performed_by', 'document_name',
            'document_file', 'comment', 'operation_uuid',  'operation_date', 'drilling_solution_ops',
            'drilling_rig_ops',
            'project', 'is_from_report'
        )
    def get_is_from_report(self, obj):
        # Если source_document не пустой, значит, транзакция из рапорта
        return bool(obj.source_document)

class TransactionCreateSerializer(serializers.ModelSerializer):
    chemical = serializers.PrimaryKeyRelatedField(queryset=Chemical.objects.all())
    from_facility = serializers.PrimaryKeyRelatedField(queryset=Facility.objects.all(), required=False, allow_null=True)
    to_facility = serializers.PrimaryKeyRelatedField(queryset=Facility.objects.all(), required=False, allow_null=True)
    performed_by = serializers.HiddenField(default=serializers.CurrentUserDefault())

    class Meta:
        model = Transaction
        fields = (
            'transaction_type', 'chemical', 'quantity', 'from_facility',
            'to_facility', 'comment', 'document_name', 'document_file', 'performed_by'
        )
    
    def validate(self, data):
        ttype = data.get('transaction_type')
        from_f = data.get('from_facility')
        to_f = data.get('to_facility')

        if ttype == 'add' and not to_f:
            raise serializers.ValidationError("Для поступления (add) необходимо указать объект 'to_facility'.")
        if ttype == 'consume' and not from_f:
            raise serializers.ValidationError("Для списания (consume) необходимо указать объект 'from_facility'.")
        if ttype == 'transfer' and (not from_f or not to_f):
            raise serializers.ValidationError("Для перемещения (transfer) необходимо указать оба объекта 'from_facility' и 'to_facility'.")
        
        # 'self.context' - это важная часть, убедитесь, что она есть
        user = self.context['request'].user
        if user.role == 'engineer':
            if ttype == 'consume' and from_f != user.related_facility:
                raise serializers.ValidationError("Инженер может списывать реагенты только со своего закрепленного объекта.")
            if ttype in ['add', 'transfer']:
                raise serializers.ValidationError("Инженер может выполнять только операции списания (consume).")

        return data
    
class RequisitionItemDistributionSerializer(serializers.ModelSerializer):
    facility_name = serializers.CharField(source='facility.name', read_only=True)
    
    class Meta:
        model = RequisitionItemDistribution
        fields = ['id', 'facility', 'facility_name', 'quantity']
class RequisitionItemSerializer(serializers.ModelSerializer):
    """
    Сериализатор для ОДНОЙ позиции в заявке.
    """
    # При чтении (GET) хотим видеть имя реагента, а не просто ID.
    chemical_name = serializers.CharField(source='chemical.name', read_only=True)
    unit_of_measurement = serializers.CharField(source='chemical.unit_of_measurement', read_only=True)
    distributions = RequisitionItemDistributionSerializer(many=True)

    class Meta:
        model = RequisitionItem
        fields = [
            'id', 
            'chemical', # Это поле будет принимать ID при записи (POST/PUT)
            'chemical_name', # Это поле будет отдавать имя при чтении (GET)
            'unit_of_measurement', # И единицу измерения
            'quantity', 
            'notes', 
            'received_quantity',
            'distributions'
        ]
        read_only_fields = ['received_quantity']



class RequisitionSerializer(serializers.ModelSerializer):
    """
    Главный сериализатор для заявки, который включает в себя список позиций.
    """
    # Включаем вложенные позиции. `many=True` означает, что это будет список.
    items = RequisitionItemSerializer(many=True)
    
    # Поля только для чтения, чтобы показывать имена, а не ID
    created_by_username = serializers.CharField(source='created_by.username', read_only=True)
    target_facility_name = serializers.CharField(source='target_facility.name', read_only=True)
    # Добавляем поле для имени утверждающего
    approved_by_username = serializers.CharField(source='approved_by.username', read_only=True, allow_null=True)
    
    class Meta:
        model = Requisition
        fields = [
            'id', 'req_number', 'status', 'created_by', 'created_by_username', 'target_facility',
            'target_facility_name', 'required_date', 'comment', 'created_at', 'items',
            'approved_by', 'approved_by_username', 'submitted_at', 'approved_at'
        ]
        read_only_fields = ['created_by','created_by_username','approved_by',]

    def create(self, validated_data):
        items_data = validated_data.pop('items')
        requisition = Requisition.objects.create(**validated_data)
        
        for item_data in items_data:
            distributions_data = item_data.pop('distributions')
            # Создаем "родительскую" позицию
            req_item = RequisitionItem.objects.create(requisition=requisition, **item_data)
            # Создаем ее дочерние распределения
            for dist_data in distributions_data:
                RequisitionItemDistribution.objects.create(requisition_item=req_item, **dist_data)
                
        return requisition

    def update(self, instance, validated_data):
        items_data = validated_data.pop('items', None)
        instance = super().update(instance, validated_data)
        
        if items_data is not None:
            instance.items.all().delete() # Удаляем старые позиции (и их распределения удалятся каскадно)
            for item_data in items_data:
                distributions_data = item_data.pop('distributions')
                req_item = RequisitionItem.objects.create(requisition=instance, **item_data)
                for dist_data in distributions_data:
                    RequisitionItemDistribution.objects.create(requisition_item=req_item, **dist_data)
                    
        return instance
    
    def validate_req_number(self, value):
        # Проверяем, существует ли уже заявка с таким номером
        # Если мы редактируем, нужно исключить саму себя из проверки
        query = Requisition.objects.filter(req_number=value)
        if self.instance:
            query = query.exclude(pk=self.instance.pk)
        if query.exists():
            raise serializers.ValidationError("Заявка с таким номером уже существует.")
        return value
    
class WellClosureItemSerializer(serializers.ModelSerializer):
    """Сериализатор для одной позиции в 'Закрытии скважины'."""
    # Поля только для чтения, чтобы показывать имена
    chemical_name = serializers.CharField(source='chemical.name', read_only=True)
    unit_of_measurement = serializers.CharField(source='chemical.unit_of_measurement', read_only=True)

    class Meta:
        model = WellClosureItem
        fields = [
            'id', 
            'chemical', # Принимает ID, отдает ID
            'chemical_name', 
            'unit_of_measurement',
            'actual_quantity', 
            'closed_quantity'
        ] 

class WellClosureSerializer(serializers.ModelSerializer):
    """Главный сериализатор для 'Закрытия скважины', включая вложенные позиции."""
    items = WellClosureItemSerializer(many=True)
    created_by_username = serializers.CharField(source='created_by.username', read_only=True)
    facility_name = serializers.CharField(source='facility.name', read_only=True, allow_null=True)
    class Meta:
        model = WellClosure
        fields = [
            'facility', 'facility_name', # <-- НОВЫЕ ПОЛЯ
            'well_number', 'bush_number', 'status', 'start_date', 'end_date',
            'comment', 'created_by', 'created_by_username', 'created_at', 'updated_at', 'items'
        ]
        read_only_fields = ['id','created_by', 'created_by_username', 'created_at', 'updated_at', 'facility_name']

    # Переопределяем create и update для работы с вложенными `items`
    def create(self, validated_data):
        items_data = validated_data.pop('items')
        closure = WellClosure.objects.create(**validated_data)
        for item_data in items_data:
            WellClosureItem.objects.create(well_closure=closure, **item_data)
        return closure
    
    @transaction.atomic
    def update(self, instance, validated_data):
        items_data = validated_data.pop('items', None)

        instance = super().update(instance, validated_data)

        if items_data is not None:
            instance.items.all().delete()

            for item_data in items_data:
                item_data.pop('id', None)

                WellClosureItem.objects.create(
                    well_closure=instance,
                    **item_data
                )

        return instance
    
# class DailyConsumptionSerializer(serializers.ModelSerializer):
#     chemical_name = serializers.CharField(source='chemical.name', read_only=True)
#     unit = serializers.CharField(source='chemical.unit_of_measurement', read_only=True)

#     class Meta:
#         model = DailyConsumption
#         fields = ['chemical_name', 'unit', 'quantity', 'cost']

# class DailyReportSerializer(serializers.ModelSerializer):
#     consumptions = DailyConsumptionSerializer(many=True, read_only=True)
#     uploaded_by_username = serializers.CharField(source='uploaded_by.username', read_only=True)
#     facility_name = serializers.CharField(source='facility.name', read_only=True)

#     class Meta:
#         model = DailyReport
#         fields = [
#             'id', 'facility', 'facility_name', 'report_date', 'work_description', 
#             'status', 'error_message', 'uploaded_by_username', 'uploaded_at', 
#             'report_file', 'consumptions'
#         ]
#         # `report_file` - обязателен при создании, но не при чтении
#         extra_kwargs = {'report_file': {'write_only': True, 'required': True}}

class BudgetLineSerializer(serializers.ModelSerializer):
    chemical_name = serializers.CharField(source='chemical.name', read_only=True)

    class Meta:
        model = BudgetLine
        fields = ['id', 'chemical', 'chemical_name', 'planned_quantity']

class ProjectSerializer(serializers.ModelSerializer):
    budget_lines = BudgetLineSerializer(many=True)
    facility_name = serializers.CharField(source='facility.name', read_only=True)

    class Meta:
        model = Project
        fields = [
            'id', 'name', 'facility', 'facility_name', 'start_date', 'end_date',
            'status', 'budget_lines'
        ]
    
    # Логика для создания/обновления вложенных строк бюджета
    def create(self, validated_data):
        lines_data = validated_data.pop('budget_lines')
        project = Project.objects.create(**validated_data)
        for line_data in lines_data:
            BudgetLine.objects.create(project=project, **line_data)
        return project

    def update(self, instance, validated_data):
        lines_data = validated_data.pop('budget_lines', None)
        instance = super().update(instance, validated_data)
        if lines_data is not None:
            instance.budget_lines.all().delete()
            for line_data in lines_data:
                BudgetLine.objects.create(project=instance, **line_data)
        return instance